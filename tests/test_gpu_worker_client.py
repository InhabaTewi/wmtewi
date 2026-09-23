from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from apps.gpu_worker.client import (
    CloudAuthenticationError,
    CloudProtocolError,
    CloudResponseError,
    CloudUnavailableError,
    WorkerCloudClient,
)
from apps.gpu_worker.config import WorkerSettings
from packages.schemas.worker import WorkerHeartbeatRequest, WorkerRegisterRequest


def settings() -> WorkerSettings:
    return WorkerSettings(
        cloud_base_url="https://cloud.example/tewi/",
        service_token="secret-token",
        _env_file=None,
    )


def response_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "worker": {
            "id": str(uuid4()),
            "worker_id": "home-5090-01",
            "status": "ONLINE",
            "effective_status": "ONLINE",
            "capabilities": ["llm.inference"],
            "gpu_name": "NVIDIA GPU",
            "gpu_count": 1,
            "vram_total_mb": 1000,
            "vram_used_mb": 100,
            "vram_free_mb": 900,
            "loaded_model": None,
            "model_version": None,
            "model_alias": None,
            "last_heartbeat_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
        "heartbeat_interval_seconds": 10,
        "server_time": now.isoformat(),
    }


@pytest.mark.asyncio
async def test_worker_client_registers_and_joins_prefix_urls() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url == "https://cloud.example/tewi/api/workers/register"
        assert request.headers["authorization"] == "Bearer secret-token"
        assert request.json() if False else b"home-5090-01" in request.content
        return httpx.Response(200, json=response_payload())

    client = WorkerCloudClient(settings(), transport=httpx.MockTransport(handler))
    response = await client.register(WorkerRegisterRequest(worker_id="home-5090-01"))
    repeated = await client.register(WorkerRegisterRequest(worker_id="home-5090-01"))

    assert response.worker.worker_id == "home-5090-01"
    assert repeated.worker.worker_id == "home-5090-01"
    assert requests == 2
    await client.aclose()
    assert client._client.is_closed


@pytest.mark.asyncio
async def test_worker_client_heartbeats_and_maps_protocol_errors() -> None:
    statuses = iter([200, 401, 404, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://cloud.example/tewi/api/workers/home-5090-01/heartbeat"
        return httpx.Response(next(statuses), json=response_payload())

    client = WorkerCloudClient(settings(), transport=httpx.MockTransport(handler))
    request = WorkerHeartbeatRequest(capabilities=["llm.inference"])

    assert (await client.heartbeat("home-5090-01", request)).worker.worker_id == "home-5090-01"
    with pytest.raises(CloudAuthenticationError):
        await client.heartbeat("home-5090-01", request)
    with pytest.raises(CloudProtocolError):
        await client.heartbeat("home-5090-01", request)
    with pytest.raises(CloudUnavailableError):
        await client.heartbeat("home-5090-01", request)
    await client.aclose()


@pytest.mark.asyncio
async def test_worker_client_handles_timeout_and_malformed_response_without_secret() -> None:
    timeout_client = WorkerCloudClient(
        settings(),
        transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout"))),
    )
    with pytest.raises(CloudUnavailableError) as timeout:
        await timeout_client.register(WorkerRegisterRequest(worker_id="home-5090-01"))
    assert "secret-token" not in str(timeout.value)
    await timeout_client.aclose()

    malformed_client = WorkerCloudClient(
        settings(), transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    )
    with pytest.raises(CloudResponseError):
        await malformed_client.register(WorkerRegisterRequest(worker_id="home-5090-01"))
    await malformed_client.aclose()