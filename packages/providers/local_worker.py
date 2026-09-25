import asyncio
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from packages.inference_jobs.service import InferenceJobService
from packages.persistence.models import InferenceJob
from packages.providers.base import ProviderResponseError, ProviderUnavailableError
from packages.schemas.inference import InferenceJobPayload, InferenceJobStatus
from packages.schemas.worker import WorkerCapability, WorkerInfo, WorkerStatus
from packages.worker_nodes.service import WorkerRegistryService


class LocalWorkerProvider:
    name = "local-worker"
    model_id = "local-worker"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        request_timeout_seconds: float,
        lease_seconds: int,
        ttl_seconds: int,
        result_poll_interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.request_timeout_seconds = request_timeout_seconds
        self.lease_seconds = lease_seconds
        self.ttl_seconds = ttl_seconds
        self.result_poll_interval_seconds = result_poll_interval_seconds

    async def health(self) -> bool:
        return await asyncio.to_thread(self._select_worker) is not None

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        worker = await asyncio.to_thread(self._select_worker)
        if worker is None:
            raise ProviderUnavailableError("No ONLINE local inference worker is available")
        job_id = await asyncio.to_thread(
            self._create_job,
            worker,
            trace_id,
            messages,
            response_schema,
        )
        started = perf_counter()
        while perf_counter() - started < self.request_timeout_seconds:
            job = await asyncio.to_thread(self._get_job, job_id)
            if job.status == InferenceJobStatus.SUCCEEDED:
                assert job.result is not None
                try:
                    return response_schema.model_validate(job.result.structured_output)
                except ValidationError as exc:
                    raise ProviderResponseError("Local Worker returned invalid structured output") from exc
            if job.status in {InferenceJobStatus.FAILED, InferenceJobStatus.EXPIRED}:
                raise ProviderUnavailableError(job.error_message or "Local inference job failed")
            await asyncio.sleep(self.result_poll_interval_seconds)
        await asyncio.to_thread(self._expire_job, job_id)
        raise ProviderUnavailableError("Local inference job timed out")

    async def aclose(self) -> None:
        return None

    def _select_worker(self) -> WorkerInfo | None:
        with self.session_factory() as session:
            workers = WorkerRegistryService(session).list()
            eligible = [
                worker
                for worker in workers
                if worker.effective_status == WorkerStatus.ONLINE
                and WorkerCapability.LLM_INFERENCE in worker.capabilities
                and worker.loaded_model is not None
            ]
            if not eligible:
                return None
            return min(eligible, key=lambda worker: worker.worker_id)

    def _create_job(
        self,
        worker: WorkerInfo,
        trace_id: str,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
    ):
        with self.session_factory() as session:
            job = InferenceJobService(
                session,
                lease_seconds=self.lease_seconds,
                ttl_seconds=self.ttl_seconds,
            ).create(
                request_id=uuid4(),
                trace_id=__import__("uuid").UUID(trace_id),
                target_worker_id=worker.worker_id,
                payload=InferenceJobPayload(
                    messages=messages,
                    response_schema={"name": response_schema.__name__, "schema": response_schema.model_json_schema()},
                ),
                model_alias=worker.model_alias,
                model_version_requirement=worker.model_version,
            )
            session.commit()
            return job.id

    def _get_job(self, job_id):
        with self.session_factory() as session:
            return InferenceJobService(session).get(job_id)

    def _expire_job(self, job_id) -> None:
        with self.session_factory() as session:
            job = session.get(InferenceJob, job_id)
            if job is not None and job.status in {InferenceJobStatus.QUEUED.value, InferenceJobStatus.CLAIMED.value}:
                job.status = InferenceJobStatus.EXPIRED.value
                job.error_code = "JOB_EXPIRED"
                job.error_message = "Local inference job timed out"
                job.failed_at = datetime.now(UTC)
                session.commit()