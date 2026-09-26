from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from packages.chat.service import ChatService
from packages.context_builder.builder import ContextBuilder
from packages.inference_jobs.service import InferenceJobLeaseError, InferenceJobService
from packages.persona.repository import PersonaRepository
from packages.persistence.models import InferenceJob, InteractionTrace, Message, WorkerNode
from packages.providers.base import (
    ProviderConfigurationError,
    ProviderFailure,
    ProviderFailureKind,
    ProviderTimeoutError,
)
from packages.providers.failover import FailoverPolicy
from packages.providers.local_cloud_fallback import PreferLocalWithCloudFallbackProvider
from packages.providers.local_worker import LocalWorkerProvider
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse, ChatRequest
from packages.schemas.inference import InferenceJobCompleteRequest, InferenceJobPayload, InferenceJobResult, InferenceJobStatus
from packages.schemas.memory import MemoryCandidate
from packages.schemas.persona import PersonaPackage


class StubProvider:
    def __init__(self, name: str, result: AgentResponse | Exception, *, healthy: bool = True) -> None:
        self.name = name
        self.model_id = f"{name}-model"
        self.result = result
        self.healthy = healthy
        self.calls: list[tuple[list[dict[str, str]], type[BaseModel], str]] = []

    async def health(self) -> bool:
        return self.healthy

    async def generate(self, messages, response_schema, trace_id):
        self.calls.append((messages, response_schema, trace_id))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def local_failure(kind: ProviderFailureKind) -> ProviderFailure:
    return ProviderFailure(kind=kind, provider="local-worker", safe_message="local failed")


def prefer_router(local: StubProvider, cloud: StubProvider) -> ProviderRouter:
    return ProviderRouter(external=cloud, local=local, mode="prefer_local_with_cloud_fallback")


@pytest.mark.asyncio
async def test_local_success_does_not_call_cloud_and_records_local_path() -> None:
    local = StubProvider("local-worker", AgentResponse(speech="local"))
    cloud = StubProvider("external-openai", AgentResponse(speech="cloud"))

    response, route = await prefer_router(local, cloud).generate([{"role": "user", "content": "x"}], AgentResponse, "trace")

    assert response.speech == "local"
    assert len(local.calls) == 1
    assert cloud.calls == []
    assert route.provider is local
    assert route.runtime_mode == "local"
    assert route.trace_metadata == {
        "provider_path": "local",
        "primary_provider": "local-worker",
        "primary_duration_ms": route.trace_metadata["primary_duration_ms"],
        "failover_decision_ms": None,
        "fallback_attempted": False,
        "final_provider": "local-worker",
        "total_provider_ms": route.trace_metadata["total_provider_ms"],
    }


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
async def test_eligible_local_failures_call_cloud_once_with_same_messages_and_schema(kind) -> None:
    local = StubProvider("local-worker", local_failure(kind))
    cloud = StubProvider("external-openai", AgentResponse(speech="cloud"))
    messages = [{"role": "system", "content": "once"}, {"role": "user", "content": "x"}]

    response, route = await prefer_router(local, cloud).generate(messages, AgentResponse, "trace")

    assert response.speech == "cloud"
    assert len(local.calls) == 1
    assert len(cloud.calls) == 1
    assert local.calls[0][0] is messages
    assert cloud.calls[0][0] is messages
    assert local.calls[0][1] is AgentResponse
    assert cloud.calls[0][1] is AgentResponse
    assert route.provider is cloud
    assert route.runtime_mode == "api"
    assert route.trace_metadata["primary_failure_kind"] == kind.value
    assert route.trace_metadata["fallback_attempted"] is True
    assert route.trace_metadata["final_provider"] == "external-openai"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        ProviderFailureKind.MODEL_MISMATCH,
        ProviderFailureKind.INVALID_STRUCTURED_RESULT,
        ProviderFailureKind.PROVIDER_AUTH_ERROR,
        ProviderFailureKind.PROVIDER_CONFIGURATION_ERROR,
        ProviderFailureKind.INVALID_REQUEST,
        ProviderFailureKind.STORAGE_ERROR,
    ],
)
async def test_hard_local_failures_do_not_call_cloud(kind) -> None:
    local = StubProvider("local-worker", local_failure(kind))
    cloud = StubProvider("external-openai", AgentResponse(speech="cloud"))

    with pytest.raises(ProviderFailure) as raised:
        await prefer_router(local, cloud).generate([], AgentResponse, "trace")

    assert raised.value.kind is kind
    assert len(local.calls) == 1
    assert cloud.calls == []


@pytest.mark.asyncio
async def test_unknown_local_exception_fails_closed_without_cloud() -> None:
    local = StubProvider("local-worker", RuntimeError("unexpected"))
    cloud = StubProvider("external-openai", AgentResponse(speech="cloud"))

    with pytest.raises(RuntimeError, match="unexpected"):
        await prefer_router(local, cloud).generate([], AgentResponse, "trace")

    assert cloud.calls == []


@pytest.mark.asyncio
async def test_cloud_failure_is_final_and_local_is_not_retried() -> None:
    local = StubProvider("local-worker", local_failure(ProviderFailureKind.WORKER_UNAVAILABLE))
    cloud = StubProvider("external-openai", ProviderTimeoutError("cloud timed out"))

    with pytest.raises(ProviderTimeoutError, match="cloud timed out"):
        await prefer_router(local, cloud).generate([], AgentResponse, "trace")

    assert len(local.calls) == 1
    assert len(cloud.calls) == 1


@pytest.mark.asyncio
async def test_chat_records_failover_attempt_metadata_when_cloud_fallback_fails(session) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(persona_id="inaba", version="v1", display_name="Inaba", system_prompt="Stay in character.")
    )
    local = StubProvider("local-worker", local_failure(ProviderFailureKind.WORKER_UNAVAILABLE))
    cloud = StubProvider("external-openai", ProviderTimeoutError("cloud timed out"))

    with pytest.raises(ProviderTimeoutError):
        await ChatService(session, prefer_router(local, cloud)).chat(
            ChatRequest(channel="web", session_id="failed-fallback", user_id="fallback-user", text="hello")
        )

    trace = session.scalar(select(InteractionTrace))
    assert trace is not None
    assert trace.payload["provider_path"] == "local_to_cloud_fallback"
    assert trace.payload["primary_failure_kind"] == ProviderFailureKind.WORKER_UNAVAILABLE.value
    assert trace.payload["fallback_failure_type"] == "ProviderTimeoutError"
    assert session.scalars(select(Message).where(Message.session_id == "failed-fallback")).all() == []


@pytest.mark.asyncio
async def test_chat_builds_context_once_persists_one_result_and_records_fallback_trace(session, monkeypatch) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(persona_id="inaba", version="v1", display_name="Inaba", system_prompt="Stay in character.")
    )
    local = StubProvider("local-worker", local_failure(ProviderFailureKind.WORKER_UNAVAILABLE))
    cloud = StubProvider(
        "external-openai",
        AgentResponse(
            speech="cloud result",
            memory_candidates=[MemoryCandidate(type="preference", content="likes tea", scope="shared")],
        ),
    )
    build_calls = 0
    original_build = ContextBuilder.build

    def counted_build(self, *args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return original_build(self, *args, **kwargs)

    monkeypatch.setattr(ContextBuilder, "build", counted_build)
    result = await ChatService(session, prefer_router(local, cloud)).chat(
        ChatRequest(channel="web", session_id="fallback-session", user_id="fallback-user", text="hello")
    )

    messages = session.scalars(select(Message).where(Message.session_id == "fallback-session")).all()
    trace = session.get(InteractionTrace, result.trace_id)
    assert build_calls == 1
    assert result.provider == "external-openai"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert len(local.calls) == 1
    assert len(cloud.calls) == 1
    assert local.calls[0][0] == cloud.calls[0][0]
    assert trace is not None
    assert trace.payload["provider_path"] == "local_to_cloud_fallback"
    assert trace.payload["primary_failure_kind"] == ProviderFailureKind.WORKER_UNAVAILABLE.value
    assert trace.payload["fallback_attempted"] is True
    assert trace.payload["final_provider"] == "external-openai"


def test_stale_local_completion_is_rejected_after_timeout_terminal_state(session) -> None:
    service = InferenceJobService(session, lease_seconds=30, ttl_seconds=60)
    trace_id = uuid4()
    session.add(InteractionTrace(id=trace_id, event_id=uuid4(), payload={}))
    job = service.create(
        request_id=uuid4(),
        trace_id=trace_id,
        target_worker_id="home-5090-01",
        payload=InferenceJobPayload(messages=[{"role": "user", "content": "x"}], response_schema={"name": "AgentResponse"}),
        model_alias="local-dev",
        model_version_requirement="revision",
        now=datetime.now(UTC),
    )
    claim = service.claim("home-5090-01", datetime.now(UTC))
    assert claim is not None
    job_record = session.get(InferenceJob, job.id)
    assert job_record is not None
    job_record.status = InferenceJobStatus.EXPIRED.value
    job_record.error_code = "JOB_EXPIRED"
    session.flush()

    with pytest.raises(InferenceJobLeaseError):
        service.complete(
            job.id,
            InferenceJobCompleteRequest(
                worker_id="home-5090-01",
                claim_token=claim.claim_token,
                result=InferenceJobResult(structured_output={"speech": "stale local"}),
            ),
            datetime.now(UTC) + timedelta(seconds=1),
        )

    assert service.get(job.id).status is InferenceJobStatus.EXPIRED
    assert service.get(job.id).result is None


@pytest.mark.asyncio
async def test_claimed_local_inference_timeout_falls_back_and_rejects_stale_worker_completion(session, monkeypatch) -> None:
    session.add(
        WorkerNode(
            worker_id="home-5090-01",
            node_name="Home 5090",
            status="ONLINE",
            capabilities=["llm.inference"],
            loaded_model="Qwen/Qwen3.5-9B",
            model_alias="local-dev",
            model_version="revision",
            last_heartbeat_at=datetime.now(UTC),
        )
    )
    session.commit()
    session_factory = sessionmaker(bind=session.bind)
    local = LocalWorkerProvider(
        session_factory,
        claim_timeout_seconds=0.01,
        inference_timeout_seconds=0.01,
        lease_seconds=30,
        ttl_seconds=60,
        result_poll_interval_seconds=0.001,
    )
    claim_token: str | None = None
    job_id = None
    original_create = local._create_job

    def create_and_claim(*args, **kwargs):
        nonlocal claim_token, job_id
        job_id = original_create(*args, **kwargs)
        with session_factory() as claim_session:
            claim = InferenceJobService(claim_session, lease_seconds=30).claim("home-5090-01")
            assert claim is not None
            claim_token = claim.claim_token
            claim_session.commit()
        return job_id

    monkeypatch.setattr(local, "_create_job", create_and_claim)
    cloud = StubProvider("external-openai", AgentResponse(speech="cloud result"))
    response, route = await prefer_router(local, cloud).generate(
        [{"role": "user", "content": "x"}], AgentResponse, str(uuid4())
    )

    assert response.speech == "cloud result"
    assert route.trace_metadata["primary_failure_kind"] == ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT.value
    assert len(cloud.calls) == 1
    assert job_id is not None
    assert claim_token is not None
    with session_factory() as check_session:
        service = InferenceJobService(check_session)
        assert service.get(job_id).status is InferenceJobStatus.EXPIRED
        with pytest.raises(InferenceJobLeaseError):
            service.complete(
                job_id,
                InferenceJobCompleteRequest(
                    worker_id="home-5090-01",
                    claim_token=claim_token,
                    result=InferenceJobResult(structured_output={"speech": "late local result"}),
                ),
            )
        assert service.get(job_id).result is None