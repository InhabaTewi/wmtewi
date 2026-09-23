from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr

from apps.control_api import dependencies
from apps.control_api.main import app
from packages.schemas.worker import WorkerHeartbeatRequest, WorkerRegisterRequest, WorkerStatus
from packages.worker_nodes.service import WorkerRegistryService


def register_request(**overrides) -> WorkerRegisterRequest:
    values = {
        "worker_id": "home-5090-01",
        "capabilities": ["llm.inference"],
        "gpu_name": "RTX 5090",
        "gpu_count": 1,
        "vram_total_mb": 32768,
        "vram_used_mb": 2048,
        "vram_free_mb": 30720,
        "loaded_model": "qwen-base",
        "model_version": "base",
        "model_alias": "development",
    }
    values.update(overrides)
    return WorkerRegisterRequest(**values)


def test_register_duplicate_upserts_worker_and_persists_metadata(session) -> None:
    service = WorkerRegistryService(session, heartbeat_timeout_seconds=45)
    first = service.register(register_request())
    second = service.register(register_request(status=WorkerStatus.TRAINING, vram_used_mb=4096))
    session.commit()

    assert first.id == second.id
    assert second.status == WorkerStatus.TRAINING
    assert second.capabilities == ["llm.inference"]
    assert second.vram_used_mb == 4096
    assert len(service.list()) == 1


def test_heartbeat_is_idempotent_and_effective_status_goes_offline(session) -> None:
    service = WorkerRegistryService(session, heartbeat_timeout_seconds=45)
    now = datetime(2026, 9, 24, tzinfo=UTC)
    service.register(register_request(), now)
    heartbeat = WorkerHeartbeatRequest(
        status=WorkerStatus.BUSY,
        capabilities=["llm.inference"],
        gpu_name="RTX 5090",
        gpu_count=1,
        vram_total_mb=32768,
        vram_used_mb=8192,
        vram_free_mb=24576,
        loaded_model="qwen-base",
        model_version="base",
        model_alias="development",
    )
    active = service.heartbeat("home-5090-01", heartbeat, now + timedelta(seconds=1))
    same = service.heartbeat("home-5090-01", heartbeat, now + timedelta(seconds=2))

    assert active.status == WorkerStatus.BUSY
    assert active.effective_status == WorkerStatus.BUSY
    assert same.id == active.id
    assert service.get("home-5090-01", now + timedelta(seconds=48)).effective_status == WorkerStatus.OFFLINE


def test_worker_api_requires_auth_and_exposes_registry(session, monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))

    def override_session():
        yield session

    app.dependency_overrides[dependencies.get_session] = override_session
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-service-token"}
    payload = register_request().model_dump(mode="json")
    try:
        assert client.post("/api/workers/register", json=payload).status_code == 401
        registered = client.post("/api/workers/register", headers=headers, json=payload)
        assert registered.status_code == 200
        assert registered.json()["heartbeat_interval_seconds"] == 15
        heartbeat = client.post(
            "/api/workers/home-5090-01/heartbeat",
            headers=headers,
            json={"status": "ONLINE", "capabilities": ["llm.inference"], "gpu_count": 1},
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["worker"]["effective_status"] == "ONLINE"
        assert client.get("/api/workers", headers=headers).json()[0]["worker_id"] == "home-5090-01"
        assert client.get("/api/workers/home-5090-01", headers=headers).status_code == 200
        assert client.get("/api/workers/missing", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()