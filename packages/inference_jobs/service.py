import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from packages.persistence.models import InferenceJob
from packages.schemas.inference import (
    InferenceErrorCode,
    InferenceJobClaim,
    InferenceJobCompleteRequest,
    InferenceJobFailRequest,
    InferenceJobInfo,
    InferenceJobPayload,
    InferenceJobResult,
    InferenceJobStatus,
)


class InferenceJobNotFoundError(Exception):
    pass


class InferenceJobLeaseError(Exception):
    pass


class InferenceJobService:
    def __init__(self, session: Session, *, lease_seconds: int = 150, ttl_seconds: int = 180) -> None:
        self.session = session
        self.lease_seconds = lease_seconds
        self.ttl_seconds = ttl_seconds

    def create(
        self,
        *,
        request_id: UUID,
        trace_id: UUID,
        target_worker_id: str,
        payload: InferenceJobPayload,
        model_alias: str | None,
        model_version_requirement: str | None,
        now: datetime | None = None,
    ) -> InferenceJobInfo:
        timestamp = now or datetime.now(UTC)
        job = InferenceJob(
            request_id=request_id,
            trace_id=trace_id,
            status=InferenceJobStatus.QUEUED.value,
            target_worker_id=target_worker_id,
            model_alias=model_alias,
            model_version_requirement=model_version_requirement,
            request_payload=payload.model_dump(mode="json"),
            queued_at=timestamp,
            expires_at=timestamp + timedelta(seconds=self.ttl_seconds),
        )
        self.session.add(job)
        self.session.flush()
        return self._to_info(job)

    def claim(self, worker_id: str, now: datetime | None = None) -> InferenceJobClaim | None:
        timestamp = now or datetime.now(UTC)
        self.expire_due(timestamp)
        query: Select[tuple[InferenceJob]] = (
            select(InferenceJob)
            .where(
                InferenceJob.status == InferenceJobStatus.QUEUED.value,
                InferenceJob.target_worker_id == worker_id,
                InferenceJob.expires_at > timestamp,
            )
            .order_by(InferenceJob.created_at, InferenceJob.id)
            .limit(1)
        )
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = self.session.scalar(query)
        if job is None:
            return None
        token = secrets.token_urlsafe(32)
        job.status = InferenceJobStatus.CLAIMED.value
        job.claimed_by_worker_id = worker_id
        job.claim_token = token
        job.claimed_at = timestamp
        job.lease_expires_at = timestamp + timedelta(seconds=self.lease_seconds)
        self.session.flush()
        return InferenceJobClaim(
            id=job.id,
            request_id=job.request_id,
            trace_id=job.trace_id,
            worker_id=worker_id,
            claim_token=token,
            lease_expires_at=job.lease_expires_at,
            payload=InferenceJobPayload.model_validate(job.request_payload),
            model_alias=job.model_alias,
            model_version_requirement=job.model_version_requirement,
        )

    def complete(self, job_id: UUID, request: InferenceJobCompleteRequest, now: datetime | None = None) -> InferenceJobInfo:
        job = self._valid_claim(job_id, request.worker_id, request.claim_token, now)
        timestamp = now or datetime.now(UTC)
        job.status = InferenceJobStatus.SUCCEEDED.value
        job.result_payload = request.result.model_dump(mode="json")
        job.completed_at = timestamp
        self.session.flush()
        return self._to_info(job)

    def fail(self, job_id: UUID, request: InferenceJobFailRequest, now: datetime | None = None) -> InferenceJobInfo:
        job = self._valid_claim(job_id, request.worker_id, request.claim_token, now)
        timestamp = now or datetime.now(UTC)
        job.status = InferenceJobStatus.FAILED.value
        job.error_code = request.error_code.value
        job.error_message = request.error_message
        job.failed_at = timestamp
        self.session.flush()
        return self._to_info(job)

    def get(self, job_id: UUID) -> InferenceJobInfo:
        job = self.session.get(InferenceJob, job_id)
        if job is None:
            raise InferenceJobNotFoundError(job_id)
        return self._to_info(job)

    def expire_due(self, now: datetime | None = None) -> int:
        timestamp = now or datetime.now(UTC)
        jobs = self.session.scalars(
            select(InferenceJob).where(
                ((InferenceJob.status == InferenceJobStatus.QUEUED.value) & (InferenceJob.expires_at <= timestamp))
                | ((InferenceJob.status == InferenceJobStatus.CLAIMED.value) & (InferenceJob.lease_expires_at <= timestamp))
            )
        ).all()
        for job in jobs:
            job.status = InferenceJobStatus.EXPIRED.value
            job.error_code = InferenceErrorCode.JOB_EXPIRED.value
            job.error_message = "Inference job lease or TTL expired"
            job.failed_at = timestamp
        self.session.flush()
        return len(jobs)

    def cleanup(self, older_than: datetime) -> int:
        terminal = {
            InferenceJobStatus.SUCCEEDED.value,
            InferenceJobStatus.FAILED.value,
            InferenceJobStatus.EXPIRED.value,
        }
        jobs = self.session.scalars(
            select(InferenceJob).where(InferenceJob.status.in_(terminal), InferenceJob.updated_at < older_than)
        ).all()
        for job in jobs:
            self.session.delete(job)
        self.session.flush()
        return len(jobs)

    def _valid_claim(self, job_id: UUID, worker_id: str, token: str, now: datetime | None) -> InferenceJob:
        timestamp = now or datetime.now(UTC)
        self.expire_due(timestamp)
        job = self.session.get(InferenceJob, job_id)
        if job is None:
            raise InferenceJobNotFoundError(job_id)
        if (
            job.status != InferenceJobStatus.CLAIMED.value
            or job.claimed_by_worker_id != worker_id
            or job.claim_token is None
            or not secrets.compare_digest(job.claim_token, token)
            or job.lease_expires_at is None
            or self._as_utc(job.lease_expires_at) <= timestamp
        ):
            raise InferenceJobLeaseError(job_id)
        return job

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    @staticmethod
    def _to_info(job: InferenceJob) -> InferenceJobInfo:
        return InferenceJobInfo(
            id=job.id,
            request_id=job.request_id,
            trace_id=job.trace_id,
            status=InferenceJobStatus(job.status),
            target_worker_id=job.target_worker_id,
            model_alias=job.model_alias,
            model_version_requirement=job.model_version_requirement,
            claimed_by_worker_id=job.claimed_by_worker_id,
            result=InferenceJobResult.model_validate(job.result_payload) if job.result_payload else None,
            error_code=InferenceErrorCode(job.error_code) if job.error_code else None,
            error_message=job.error_message,
            created_at=job.created_at,
            queued_at=job.queued_at,
            claimed_at=job.claimed_at,
            completed_at=job.completed_at,
            failed_at=job.failed_at,
            expires_at=job.expires_at,
            lease_expires_at=job.lease_expires_at,
        )