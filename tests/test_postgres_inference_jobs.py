import os
import queue
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from packages.inference_jobs.service import InferenceJobService
from packages.persistence.models import InferenceJob
from packages.schemas.inference import InferenceJobPayload, InferenceJobStatus


INTEGRATION_URL_ENV = "POSTGRES_INTEGRATION_URL"


@pytest.fixture
def postgres_session_factory() -> tuple[sessionmaker[Session], object]:
    postgres_url = os.environ.get(INTEGRATION_URL_ENV)
    if not postgres_url:
        pytest.skip(f"set {INTEGRATION_URL_ENV} to run PostgreSQL inference-job integration tests")
    if not postgres_url.startswith("postgresql+") or "test" not in postgres_url.lower():
        pytest.fail(f"{INTEGRATION_URL_ENV} must target a dedicated PostgreSQL test database")
    engine = create_engine(postgres_url)
    try:
        yield sessionmaker(bind=engine), engine
    finally:
        engine.dispose()


def create_job(session: Session) -> UUID:
    job = InferenceJobService(session).create(
        request_id=uuid4(),
        trace_id=uuid4(),
        target_worker_id="home-5090-01",
        payload=InferenceJobPayload(
            messages=[{"role": "user", "content": "concurrent claim test"}],
            response_schema={"type": "object"},
        ),
        model_alias="local-dev",
        model_version_requirement="revision",
        now=datetime.now(UTC),
    )
    session.commit()
    return job.id


def test_postgres_concurrent_claim_is_atomic(postgres_session_factory) -> None:
    factory, _engine = postgres_session_factory
    with factory() as creator:
        job_id = create_job(creator)

    first_session = factory()
    second_result: queue.Queue[object] = queue.Queue()
    try:
        first_claim = InferenceJobService(first_session).claim("home-5090-01")
        assert first_claim is not None

        def claim_in_second_transaction() -> None:
            with factory() as second_session:
                try:
                    claim = InferenceJobService(second_session).claim("home-5090-01")
                    second_session.commit()
                    second_result.put(claim)
                except Exception as exc:  # pragma: no cover - reported in the parent assertion
                    second_result.put(exc)

        contender = threading.Thread(target=claim_in_second_transaction)
        contender.start()
        contender.join(timeout=5)
        assert not contender.is_alive(), "SKIP LOCKED claimant unexpectedly blocked"
        second_claim = second_result.get_nowait()
        assert not isinstance(second_claim, Exception)
        assert second_claim is None

        first_session.commit()
        with factory() as verifier:
            job = verifier.get(InferenceJob, job_id)
            assert job is not None
            assert job.status == InferenceJobStatus.CLAIMED.value
            assert job.claimed_by_worker_id == "home-5090-01"
    finally:
        first_session.rollback()
        first_session.close()
        with factory() as cleanup_session:
            cleanup_session.execute(delete(InferenceJob).where(InferenceJob.id == job_id))
            cleanup_session.commit()