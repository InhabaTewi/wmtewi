import asyncio
from dataclasses import dataclass
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


@dataclass(frozen=True)
class LocalWorkerExecution:
    response: BaseModel
    timing_metadata: dict[str, int | None]


class LocalWorkerProvider:
    name = "local-worker"
    model_id = "local-worker"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        claim_timeout_seconds: float,
        inference_timeout_seconds: float,
        lease_seconds: int,
        ttl_seconds: int,
        result_poll_interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.claim_timeout_seconds = claim_timeout_seconds
        self.inference_timeout_seconds = inference_timeout_seconds
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
        return (await self.generate_with_timing(messages, response_schema, trace_id)).response

    async def generate_with_timing(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> LocalWorkerExecution:
        provider_started = perf_counter()
        selection_started = perf_counter()
        try:
            worker = await asyncio.to_thread(self._select_worker)
        except SQLAlchemyError as exc:
            raise self._failure(
                ProviderFailureKind.STORAGE_ERROR,
                "Local Worker provider is unavailable",
                exc,
                timing_metadata={"local_worker_selection_ms": self._elapsed_ms(selection_started)},
            ) from exc
        selection_ms = self._elapsed_ms(selection_started)
        if worker is None:
            failure = await self.availability_failure()
            failure.trace_metadata["local_worker_selection_ms"] = selection_ms
            raise failure
        job_create_started = perf_counter()
        try:
            job_id = await asyncio.to_thread(
                self._create_job,
                worker,
                trace_id,
                messages,
                response_schema,
            )
        except SQLAlchemyError as exc:
            raise self._failure(
                ProviderFailureKind.STORAGE_ERROR,
                "Local Worker provider is unavailable",
                exc,
                timing_metadata={
                    "local_worker_selection_ms": selection_ms,
                    "job_create_ms": self._elapsed_ms(job_create_started),
                },
            ) from exc
        claim_started = perf_counter()
        inference_started: float | None = None
        timing_metadata: dict[str, int | None] = {
            "local_worker_selection_ms": selection_ms,
            "job_create_ms": self._elapsed_ms(job_create_started),
            "job_queue_ms": None,
            "job_claim_ms": None,
            "local_inference_ms": None,
            "local_result_commit_ms": None,
        }
        while True:
            try:
                job = await asyncio.to_thread(self._get_job, job_id)
            except SQLAlchemyError as exc:
                raise self._failure(ProviderFailureKind.STORAGE_ERROR, "Local Worker provider is unavailable", exc) from exc
            if job.status == InferenceJobStatus.SUCCEEDED:
                assert job.result is not None
                if job.result.inference_ms is not None:
                    timing_metadata["local_inference_ms"] = job.result.inference_ms
                timing_metadata["total_provider_ms"] = self._elapsed_ms(provider_started)
                try:
                    return LocalWorkerExecution(
                        response=response_schema.model_validate(job.result.structured_output),
                        timing_metadata=timing_metadata,
                    )
                except ValidationError as exc:
                    raise self._failure(
                        ProviderFailureKind.INVALID_STRUCTURED_RESULT,
                        "Local Worker returned invalid structured output",
                        exc,
                        timing_metadata=timing_metadata,
                    ) from exc
            if job.status in {InferenceJobStatus.FAILED, InferenceJobStatus.EXPIRED}:
                raise self._failure_for_job(job.error_code, job.error_message, timing_metadata)
            if job.status == InferenceJobStatus.CLAIMED and inference_started is None:
                inference_started = perf_counter()
                timing_metadata["job_queue_ms"] = self._elapsed_ms(claim_started)
            timeout_seconds = self.inference_timeout_seconds if inference_started is not None else self.claim_timeout_seconds
            started = inference_started if inference_started is not None else claim_started
            if perf_counter() - started >= timeout_seconds:
                break
            await asyncio.sleep(self.result_poll_interval_seconds)
        try:
            await asyncio.to_thread(self._expire_job, job_id)
        except SQLAlchemyError as exc:
            raise self._failure(
                ProviderFailureKind.STORAGE_ERROR,
                "Local Worker provider is unavailable",
                exc,
                timing_metadata=timing_metadata,
            ) from exc
        kind = ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT if inference_started is not None else ProviderFailureKind.JOB_QUEUE_TIMEOUT
        raise self._failure(kind, "Local inference job timed out", timing_metadata=timing_metadata)

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
        timing_metadata: dict[str, int | None] | None = None,
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
        return self._failure(kind, "Local inference job failed", timing_metadata=timing_metadata)

    def _failure(
        self,
        kind: ProviderFailureKind,
        safe_message: str,
        cause: Exception | None = None,
        timing_metadata: dict[str, int | None] | None = None,
    ) -> ProviderFailure:
        return ProviderFailure(
            kind=kind,
            provider=self.name,
            safe_message=safe_message,
            cause=cause,
            trace_metadata=timing_metadata,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return round((perf_counter() - started) * 1000)

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