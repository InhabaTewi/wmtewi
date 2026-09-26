from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.persistence.models import Base, InferenceJob, WorkerNode
from packages.providers.base import ProviderFailure, ProviderFailureKind
from packages.providers.failover import FailoverPolicy
from packages.providers.local_worker import LocalWorkerProvider
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse
from packages.schemas.inference import InferenceErrorCode, InferenceJobResult, InferenceJobStatus


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.mark.parametrize(
    ("kind", "retryable", "fallback_eligible"),
    [
        (ProviderFailureKind.WORKER_UNAVAILABLE, True, True),
        (ProviderFailureKind.MODEL_UNAVAILABLE, True, True),
        (ProviderFailureKind.MODEL_MISMATCH, False, False),
        (ProviderFailureKind.JOB_QUEUE_TIMEOUT, True, True),
        (ProviderFailureKind.JOB_LEASE_EXPIRED, True, True),
        (ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT, True, True),
        (ProviderFailureKind.LOCAL_INFERENCE_ERROR, True, True),
        (ProviderFailureKind.INVALID_STRUCTURED_RESULT, False, False),
        (ProviderFailureKind.PROVIDER_AUTH_ERROR, False, False),
        (ProviderFailureKind.PROVIDER_CONFIGURATION_ERROR, False, False),
        (ProviderFailureKind.INVALID_REQUEST, False, False),
        (ProviderFailureKind.STORAGE_ERROR, False, False),
    ],
)
def test_failure_taxonomy_and_policy(kind, retryable, fallback_eligible) -> None:
    failure = ProviderFailure(kind=kind, provider="local-worker", safe_message="safe")

    decision = FailoverPolicy().decide(failure)

    assert failure.kind is kind
    assert failure.retryable is retryable
    assert failure.fallback_eligible is fallback_eligible
    assert failure.public_error_code == kind.value
    assert failure.safe_message == "safe"
    assert decision.fallback_allowed is fallback_eligible
    assert decision.failure_kind == kind.value


def _add_worker(session_factory, *, status: str = "ONLINE", loaded_model: str | None = "Qwen/Qwen3.5-9B") -> None:
    with session_factory() as session:
        session.add(
            WorkerNode(
                worker_id="home-5090-01",
                node_name="Home 5090",
                status=status,
                capabilities=["llm.inference"],
                loaded_model=loaded_model,
                model_alias="local-dev",
                model_version="revision",
                last_heartbeat_at=datetime.now(UTC),
            )
        )
        session.commit()


def _provider(session_factory, *, timeout: float = 0.01) -> LocalWorkerProvider:
    return LocalWorkerProvider(
        session_factory,
        request_timeout_seconds=timeout,
        lease_seconds=30,
        ttl_seconds=60,
        result_poll_interval_seconds=0.001,
    )


@pytest.mark.asyncio
async def test_worker_offline_maps_to_fallback_eligible_worker_unavailable(session_factory) -> None:
    failure = await _provider(session_factory).availability_failure()

    assert failure.kind is ProviderFailureKind.WORKER_UNAVAILABLE
    assert failure.fallback_eligible is True


@pytest.mark.asyncio
async def test_degraded_worker_maps_to_fallback_eligible_model_unavailable(session_factory) -> None:
    _add_worker(session_factory, status="DEGRADED", loaded_model=None)

    failure = await _provider(session_factory).availability_failure()

    assert failure.kind is ProviderFailureKind.MODEL_UNAVAILABLE
    assert failure.fallback_eligible is True


@pytest.mark.asyncio
async def test_job_timeout_maps_to_fallback_eligible_queue_timeout(session_factory) -> None:
    _add_worker(session_factory)
    with pytest.raises(ProviderFailure) as raised:
        await _provider(session_factory).generate([{"role": "user", "content": "x"}], AgentResponse, str(uuid4()))

    assert raised.value.kind is ProviderFailureKind.JOB_QUEUE_TIMEOUT
    assert raised.value.retryable is True
    assert raised.value.fallback_eligible is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "kind", "eligible"),
    [
        (InferenceErrorCode.INFERENCE_TIMEOUT, ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT, True),
        (InferenceErrorCode.INFERENCE_ERROR, ProviderFailureKind.LOCAL_INFERENCE_ERROR, True),
        (InferenceErrorCode.MODEL_MISMATCH, ProviderFailureKind.MODEL_MISMATCH, False),
        (InferenceErrorCode.INVALID_RESULT, ProviderFailureKind.INVALID_STRUCTURED_RESULT, False),
        (InferenceErrorCode.JOB_EXPIRED, ProviderFailureKind.JOB_LEASE_EXPIRED, True),
    ],
)
async def test_worker_job_errors_map_to_typed_failures(session_factory, monkeypatch, error_code, kind, eligible) -> None:
    _add_worker(session_factory)
    provider = _provider(session_factory)
    original_create = provider._create_job

    def create_and_fail(*args, **kwargs):
        job_id = original_create(*args, **kwargs)
        with session_factory() as session:
            job = session.get(InferenceJob, job_id)
            job.status = InferenceJobStatus.FAILED.value
            job.error_code = error_code.value
            job.error_message = "private worker detail"
            session.commit()
        return job_id

    monkeypatch.setattr(provider, "_create_job", create_and_fail)
    with pytest.raises(ProviderFailure) as raised:
        await provider.generate([{"role": "user", "content": "x"}], AgentResponse, str(uuid4()))

    assert raised.value.kind is kind
    assert raised.value.fallback_eligible is eligible
    assert "private worker detail" not in raised.value.safe_message


@pytest.mark.asyncio
async def test_invalid_structured_result_and_storage_error_are_not_fallback_eligible(session_factory, monkeypatch) -> None:
    _add_worker(session_factory)
    provider = _provider(session_factory)
    original_create = provider._create_job

    def create_and_succeed_invalid(*args, **kwargs):
        job_id = original_create(*args, **kwargs)
        with session_factory() as session:
            job = session.get(InferenceJob, job_id)
            job.status = InferenceJobStatus.SUCCEEDED.value
            job.result_payload = InferenceJobResult(structured_output={"unexpected": "value"}).model_dump(mode="json")
            session.commit()
        return job_id

    monkeypatch.setattr(provider, "_create_job", create_and_succeed_invalid)
    with pytest.raises(ProviderFailure) as invalid:
        await provider.generate([{"role": "user", "content": "x"}], AgentResponse, str(uuid4()))
    assert invalid.value.kind is ProviderFailureKind.INVALID_STRUCTURED_RESULT
    assert invalid.value.fallback_eligible is False

    monkeypatch.setattr(provider, "_select_worker", lambda: (_ for _ in ()).throw(SQLAlchemyError("db unavailable")))
    with pytest.raises(ProviderFailure) as storage:
        await provider.generate([{"role": "user", "content": "x"}], AgentResponse, str(uuid4()))
    assert storage.value.kind is ProviderFailureKind.STORAGE_ERROR
    assert storage.value.fallback_eligible is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        ProviderFailureKind.WORKER_UNAVAILABLE,
        ProviderFailureKind.MODEL_UNAVAILABLE,
        ProviderFailureKind.JOB_QUEUE_TIMEOUT,
        ProviderFailureKind.JOB_LEASE_EXPIRED,
        ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT,
        ProviderFailureKind.LOCAL_INFERENCE_ERROR,
    ],
)
async def test_local_mode_never_calls_external_for_fallback_eligible_failure(kind) -> None:
    external_calls = 0

    class ExternalProvider:
        async def health(self) -> bool:
            nonlocal external_calls
            external_calls += 1
            return True

    class FailingLocalProvider:
        async def health(self) -> bool:
            return False

        async def availability_failure(self) -> ProviderFailure:
            return ProviderFailure(kind=kind, provider="local-worker", safe_message="local failed")

    router = ProviderRouter(ExternalProvider(), FailingLocalProvider(), mode="local_worker")
    with pytest.raises(ProviderFailure) as raised:
        await router.generate([{"role": "user", "content": "x"}], AgentResponse, str(uuid4()))

    assert raised.value.kind is kind
    assert raised.value.fallback_eligible is True
    assert external_calls == 0