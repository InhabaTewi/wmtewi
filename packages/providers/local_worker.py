import asyncio
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.inference_jobs.service import InferenceJobService
from packages.persistence.models import InferenceJob
from packages.providers.base import ProviderFailure, ProviderFailureKind
from packages.schemas.inference import InferenceErrorCode, InferenceJobPayload, InferenceJobStatus
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
        try:
            return await asyncio.to_thread(self._select_worker) is not None
        except SQLAlchemyError:
            return False

    async def availability_failure(self) -> ProviderFailure:
        try:
            workers = await asyncio.to_thread(self._list_workers)
        except SQLAlchemyError as exc:
            return self._failure(
                ProviderFailureKind.STORAGE_ERROR,
                "Local Worker provider is unavailable",
                exc,
            )
        inference_workers = [worker for worker in workers if WorkerCapability.LLM_INFERENCE in worker.capabilities]
        if any(
            worker.effective_status == WorkerStatus.DEGRADED
            or (worker.effective_status == WorkerStatus.ONLINE and worker.loaded_model is None)
            for worker in inference_workers
        ):
            return self._failure(ProviderFailureKind.MODEL_UNAVAILABLE, "Local Worker provider is unavailable")
        return self._failure(ProviderFailureKind.WORKER_UNAVAILABLE, "Local Worker provider is unavailable")

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        try:
            worker = await asyncio.to_thread(self._select_worker)
        except SQLAlchemyError as exc:
            raise self._failure(ProviderFailureKind.STORAGE_ERROR, "Local Worker provider is unavailable", exc) from exc
        if worker is None:
            raise await self.availability_failure()
        try:
            job_id = await asyncio.to_thread(
                self._create_job,
                worker,
                trace_id,
                messages,
                response_schema,
            )
        except SQLAlchemyError as exc:
            raise self._failure(ProviderFailureKind.STORAGE_ERROR, "Local Worker provider is unavailable", exc) from exc
        started = perf_counter()
        while perf_counter() - started < self.request_timeout_seconds:
            try:
                job = await asyncio.to_thread(self._get_job, job_id)
            except SQLAlchemyError as exc:
                raise self._failure(ProviderFailureKind.STORAGE_ERROR, "Local Worker provider is unavailable", exc) from exc
            if job.status == InferenceJobStatus.SUCCEEDED:
                assert job.result is not None
                try:
                    return response_schema.model_validate(job.result.structured_output)
                except ValidationError as exc:
                    raise self._failure(
                        ProviderFailureKind.INVALID_STRUCTURED_RESULT,
                        "Local Worker returned invalid structured output",
                        exc,
                    ) from exc
            if job.status in {InferenceJobStatus.FAILED, InferenceJobStatus.EXPIRED}:
                raise self._failure_for_job(job.error_code, job.error_message)
            await asyncio.sleep(self.result_poll_interval_seconds)
        try:
            await asyncio.to_thread(self._expire_job, job_id)
        except SQLAlchemyError as exc:
            raise self._failure(ProviderFailureKind.STORAGE_ERROR, "Local Worker provider is unavailable", exc) from exc
        raise self._failure(ProviderFailureKind.JOB_QUEUE_TIMEOUT, "Local inference job timed out")

    async def aclose(self) -> None:
        return None

    def _select_worker(self) -> WorkerInfo | None:
        eligible = [
            worker
            for worker in self._list_workers()
            if worker.effective_status == WorkerStatus.ONLINE
            and WorkerCapability.LLM_INFERENCE in worker.capabilities
            and worker.loaded_model is not None
        ]
        if not eligible:
            return None
        return min(eligible, key=lambda worker: worker.worker_id)

    def _list_workers(self) -> list[WorkerInfo]:
        with self.session_factory() as session:
            return WorkerRegistryService(session).list()

    def _failure_for_job(
        self,
        error_code: InferenceErrorCode | None,
        _error_message: str | None,
    ) -> ProviderFailure:
        kind = {
            InferenceErrorCode.WORKER_UNAVAILABLE: ProviderFailureKind.WORKER_UNAVAILABLE,
            InferenceErrorCode.MODEL_UNAVAILABLE: ProviderFailureKind.MODEL_UNAVAILABLE,
            InferenceErrorCode.JOB_EXPIRED: ProviderFailureKind.JOB_LEASE_EXPIRED,
            InferenceErrorCode.INFERENCE_TIMEOUT: ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT,
            InferenceErrorCode.INFERENCE_ERROR: ProviderFailureKind.LOCAL_INFERENCE_ERROR,
            InferenceErrorCode.INVALID_RESULT: ProviderFailureKind.INVALID_STRUCTURED_RESULT,
            InferenceErrorCode.MODEL_MISMATCH: ProviderFailureKind.MODEL_MISMATCH,
        }.get(error_code, ProviderFailureKind.LOCAL_INFERENCE_ERROR)
        return self._failure(kind, "Local inference job failed")

    def _failure(
        self,
        kind: ProviderFailureKind,
        safe_message: str,
        cause: Exception | None = None,
    ) -> ProviderFailure:
        return ProviderFailure(kind=kind, provider=self.name, safe_message=safe_message, cause=cause)

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