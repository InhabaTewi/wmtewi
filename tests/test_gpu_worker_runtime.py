import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.gpu_worker.client import CloudAuthenticationError, CloudUnavailableError
from apps.gpu_worker.config import WorkerSettings
from apps.gpu_worker.gpu_probe import GpuMetadata, GpuProbeError
from apps.gpu_worker.local_model_client import LocalModelUnavailableError
from apps.gpu_worker.runtime import WorkerRuntime
from packages.schemas.chat import AgentResponse
from packages.schemas.inference import InferenceErrorCode, InferenceJobClaim, InferenceJobPayload
from apps.gpu_worker.main import parse_args, run_worker
from packages.schemas.worker import WorkerStatus


class FakeProbe:
    def __init__(self, result: GpuMetadata | Exception) -> None:
        self.result = result

    def probe(self) -> GpuMetadata:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, heartbeat_results: list[Exception | None] | None = None) -> None:
        self.register_requests = []
        self.heartbeat_requests = []
        self.heartbeat_results = heartbeat_results or []
        self.closed = False

    async def register(self, request):
        self.register_requests.append(request)
        return SimpleNamespace(heartbeat_interval_seconds=2)

    async def heartbeat(self, worker_id, request):
        self.heartbeat_requests.append((worker_id, request))
        if self.heartbeat_results:
            result = self.heartbeat_results.pop(0)
            if result is not None:
                raise result
        return SimpleNamespace(heartbeat_interval_seconds=10)

    async def aclose(self) -> None:
        self.closed = True


class FakeLocalModelClient:
    def __init__(self, result: bool | Exception) -> None:
        self.result = result
        self.closed = False

    async def health(self) -> bool:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def aclose(self) -> None:
        self.closed = True


class GeneratingLocalModelClient(FakeLocalModelClient):
    def __init__(self) -> None:
        super().__init__(True)
        self.generate_calls = []

    async def generate(self, messages):
        self.generate_calls.append(messages)
        return AgentResponse(speech="local answer")


def settings() -> WorkerSettings:
    return WorkerSettings(cloud_base_url="https://cloud.example", service_token="secret", _env_file=None)


def gpu() -> GpuMetadata:
    return GpuMetadata("NVIDIA GeForce RTX 5090", 1, 32607, 1024, 31583)


@pytest.mark.asyncio
async def test_runtime_registers_then_reports_online_and_clamps_server_interval() -> None:
    client = FakeClient()
    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()))

    await runtime.run_once()

    assert client.register_requests[0].status == WorkerStatus.REGISTERING
    assert client.heartbeat_requests[0][1].status == WorkerStatus.ONLINE
    assert client.heartbeat_requests[0][1].loaded_model is None
    assert runtime.heartbeat_interval_seconds == 5


@pytest.mark.asyncio
async def test_runtime_stays_degraded_when_gpu_probe_fails() -> None:
    client = FakeClient()
    runtime = WorkerRuntime(settings(), client, FakeProbe(GpuProbeError("nvidia-smi is not available")))

    await runtime.run_once()

    assert client.heartbeat_requests[0][1].status == WorkerStatus.DEGRADED
    assert client.heartbeat_requests[0][1].gpu_name is None


@pytest.mark.asyncio
async def test_runtime_reports_local_model_metadata_only_when_healthy() -> None:
    healthy_client = FakeClient()
    runtime = WorkerRuntime(
        settings(),
        healthy_client,
        FakeProbe(gpu()),
        local_model_client=FakeLocalModelClient(True),
    )
    await runtime.run_once()
    heartbeat = healthy_client.heartbeat_requests[0][1]
    assert heartbeat.status == WorkerStatus.ONLINE
    assert heartbeat.loaded_model == "Qwen/Qwen3.5-9B"
    assert heartbeat.model_version == "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
    assert heartbeat.model_alias == "local-dev"

    degraded_client = FakeClient()
    runtime = WorkerRuntime(
        settings(),
        degraded_client,
        FakeProbe(gpu()),
        local_model_client=FakeLocalModelClient(LocalModelUnavailableError("down")),
    )
    await runtime.run_once()
    heartbeat = degraded_client.heartbeat_requests[0][1]
    assert heartbeat.status == WorkerStatus.DEGRADED
    assert heartbeat.loaded_model is None


@pytest.mark.asyncio
async def test_runtime_reregisters_after_cloud_disconnect_without_real_sleep() -> None:
    client = FakeClient(heartbeat_results=[CloudUnavailableError("down"), None])
    stop_event = asyncio.Event()
    delays = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 2:
            stop_event.set()

    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()), sleeper=sleeper, jitter=lambda: 0)
    await runtime.run(stop_event)

    assert len(client.register_requests) == 2
    assert delays == [1.0, 5]


@pytest.mark.asyncio
async def test_runtime_fails_fast_for_auth_and_closes_client() -> None:
    class AuthClient(FakeClient):
        async def register(self, request):
            raise CloudAuthenticationError("bad credentials")

    client = AuthClient()
    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()))
    with pytest.raises(CloudAuthenticationError):
        await runtime.run_once()
    await runtime.aclose()
    assert client.closed


@pytest.mark.asyncio
async def test_runtime_stops_gracefully_when_stop_is_requested() -> None:
    client = FakeClient()
    stop_event = asyncio.Event()

    async def sleeper(_delay: float) -> None:
        stop_event.set()

    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()), sleeper=sleeper)
    await runtime.run(stop_event)
    await runtime.aclose()

    assert len(client.register_requests) == 1
    assert len(client.heartbeat_requests) == 1
    assert client.closed


@pytest.mark.asyncio
async def test_shutdown_file_stops_detached_worker_gracefully(tmp_path, monkeypatch) -> None:
    shutdown_file = tmp_path / "shutdown.signal"
    client = FakeClient()
    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()))
    monkeypatch.setattr("apps.gpu_worker.main.create_runtime", lambda _settings: runtime)
    args = parse_args(["--shutdown-file", str(shutdown_file)])

    task = asyncio.create_task(run_worker(args, settings()))
    while not client.heartbeat_requests:
        await asyncio.sleep(0)
    shutdown_file.write_text("shutdown", encoding="utf-8")

    assert await task == 0
    assert client.closed


@pytest.mark.asyncio
async def test_worker_completes_claimed_agent_response_job() -> None:
    class JobClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.completed = []

        async def complete_inference_job(self, job_id, request) -> None:
            self.completed.append((job_id, request))

    client = JobClient()
    model_client = GeneratingLocalModelClient()
    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()), local_model_client=model_client)
    claim = InferenceJobClaim(
        id=uuid4(),
        request_id=uuid4(),
        trace_id=uuid4(),
        worker_id="home-5090-01",
        claim_token="x" * 32,
        lease_expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        payload=InferenceJobPayload(
            messages=[{"role": "user", "content": "Cloud-built prompt"}],
            response_schema={"name": "AgentResponse", "schema": AgentResponse.model_json_schema()},
        ),
        model_alias="local-dev",
        model_version_requirement="c202236235762e1c871ad0ccb60c8ee5ba337b9a",
    )

    await runtime._execute_inference_job(claim)

    assert model_client.generate_calls == [claim.payload.messages]
    assert client.completed[0][1].result.structured_output["speech"] == "local answer"


@pytest.mark.asyncio
async def test_worker_rejects_claim_with_model_revision_mismatch() -> None:
    class JobClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.failed = []

        async def fail_inference_job(self, job_id, request) -> None:
            self.failed.append((job_id, request))

    client = JobClient()
    model_client = GeneratingLocalModelClient()
    runtime = WorkerRuntime(settings(), client, FakeProbe(gpu()), local_model_client=model_client)
    claim = InferenceJobClaim(
        id=uuid4(),
        request_id=uuid4(),
        trace_id=uuid4(),
        worker_id="home-5090-01",
        claim_token="x" * 32,
        lease_expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        payload=InferenceJobPayload(messages=[{"role": "user", "content": "prompt"}], response_schema={"name": "AgentResponse"}),
        model_alias="local-dev",
        model_version_requirement="different-revision",
    )

    await runtime._execute_inference_job(claim)

    assert model_client.generate_calls == []
    assert client.failed[0][1].error_code == InferenceErrorCode.MODEL_MISMATCH