from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.inference_jobs.service import InferenceJobLeaseError, InferenceJobService
from packages.persistence.models import InteractionTrace
from packages.schemas.inference import (
    InferenceErrorCode,
    InferenceJobCompleteRequest,
    InferenceJobFailRequest,
    InferenceJobPayload,
    InferenceJobResult,
    InferenceJobStatus,
)


def create_job(service: InferenceJobService, now: datetime):
    trace_id = uuid4()
    service.session.add(InteractionTrace(id=trace_id, event_id=uuid4(), payload={}))
    service.session.flush()
    return service.create(
        request_id=uuid4(),
        trace_id=trace_id,
        target_worker_id="home-5090-01",
        payload=InferenceJobPayload(messages=[{"role": "user", "content": "hello"}], response_schema={"type": "object"}),
        model_alias="local-dev",
        model_version_requirement="revision",
        now=now,
    )


def test_claim_complete_and_reject_stale_result(session) -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    service = InferenceJobService(session, lease_seconds=30)
    job = create_job(service, now)

    claim = service.claim("home-5090-01", now)
    assert claim is not None
    assert service.claim("home-5090-01", now) is None
    completed = service.complete(
        job.id,
        InferenceJobCompleteRequest(
            worker_id="home-5090-01",
            claim_token=claim.claim_token,
            result=InferenceJobResult(structured_output={"speech": "done"}, inference_ms=12),
        ),
        now + timedelta(seconds=1),
    )
    assert completed.status == InferenceJobStatus.SUCCEEDED
    assert completed.result is not None
    with pytest.raises(InferenceJobLeaseError):
        service.fail(
            job.id,
            InferenceJobFailRequest(
                worker_id="home-5090-01",
                claim_token=claim.claim_token,
                error_code=InferenceErrorCode.INFERENCE_ERROR,
                error_message="late duplicate",
            ),
            now + timedelta(seconds=2),
        )


def test_expired_claim_cannot_complete_and_can_be_cleaned_up(session) -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    service = InferenceJobService(session, lease_seconds=1)
    job = create_job(service, now)
    claim = service.claim("home-5090-01", now)
    assert claim is not None
    with pytest.raises(InferenceJobLeaseError):
        service.complete(
            job.id,
            InferenceJobCompleteRequest(
                worker_id="home-5090-01",
                claim_token=claim.claim_token,
                result=InferenceJobResult(structured_output={"speech": "late"}),
            ),
            now + timedelta(seconds=2),
        )
    assert service.get(job.id).status == InferenceJobStatus.EXPIRED
    assert service.cleanup(now + timedelta(days=1)) == 1