from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.persistence.models import Base, InferenceJob, WorkerNode
from packages.providers.base import ProviderUnavailableError
from packages.providers.local_worker import LocalWorkerProvider
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse
from packages.schemas.inference import InferenceJobResult, InferenceJobStatus


def add_worker(session) -> None:
    session.add(
        WorkerNode(
            worker_id="home-5090-01",
            node_name="Home 5090",
            status="ONLINE",
            capabilities=["llm.inference"],
            gpu_name="RTX 5090",
            vram_total_mb=32768,
            loaded_model="Qwen/Qwen3.5-9B",
            model_alias="local-dev",
            model_version="revision",
            last_heartbeat_at=datetime.now(UTC),
        )
    )
    session.commit()


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.mark.asyncio
async def test_local_worker_provider_persists_final_messages_and_validates_result(session_factory, monkeypatch) -> None:
    with session_factory() as session:
        add_worker(session)
    provider = LocalWorkerProvider(
        session_factory,
        claim_timeout_seconds=1,
        inference_timeout_seconds=1,
        lease_seconds=30,
        ttl_seconds=60,
        result_poll_interval_seconds=0.01,
    )
    final_messages = [{"role": "system", "content": "Cloud context only"}, {"role": "user", "content": "hello"}]

    def complete_job(job_id) -> None:
        with session_factory() as background_session:
            job = background_session.get(InferenceJob, job_id)
            assert job is not None
            assert job.request_payload["messages"] == final_messages
            job.status = InferenceJobStatus.SUCCEEDED.value
            job.result_payload = InferenceJobResult(structured_output={"speech": "local answer"}).model_dump(mode="json")
            background_session.commit()

    original_create = provider._create_job

    def create_and_complete(*args, **kwargs):
        job_id = original_create(*args, **kwargs)
        complete_job(job_id)
        return job_id

    monkeypatch.setattr(provider, "_create_job", create_and_complete)
    result = await provider.generate(final_messages, AgentResponse, str(uuid4()))
    assert result.speech == "local answer"


@pytest.mark.asyncio
async def test_local_worker_mode_never_falls_back_to_external(session_factory) -> None:
    provider = LocalWorkerProvider(
        session_factory,
        claim_timeout_seconds=1,
        inference_timeout_seconds=1,
        lease_seconds=30,
        ttl_seconds=60,
        result_poll_interval_seconds=0.01,
    )

    class ExternalShouldNotRun:
        async def health(self) -> bool:
            raise AssertionError("external provider must not be checked")

    router = ProviderRouter(ExternalShouldNotRun(), provider, mode="local_worker")
    with pytest.raises(ProviderUnavailableError):
        await router.select()