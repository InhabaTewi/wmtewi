from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from apps.control_api import dependencies
from apps.control_api.main import app
from packages.inference_jobs.service import InferenceJobService
from packages.persistence.models import InteractionTrace
from packages.schemas.inference import InferenceJobPayload
from packages.schemas.worker import WorkerRegisterRequest, WorkerStatus
from packages.worker_nodes.service import WorkerRegistryService


def create_job(session):
    trace_id = uuid4()
    session.add(InteractionTrace(id=trace_id, event_id=uuid4(), payload={}))
    session.flush()
    return InferenceJobService(session).create(
        request_id=uuid4(),
        trace_id=trace_id,
        target_worker_id="home-5090-01",
        payload=InferenceJobPayload(messages=[{"role": "user", "content": "hello"}], response_schema={"type": "object"}),
        model_alias="local-dev",
        model_version_requirement="revision",
        now=datetime.now(UTC),
    )


def register_eligible_worker(session) -> None:
    WorkerRegistryService(session).register(
        WorkerRegisterRequest(
            worker_id="home-5090-01",
            status=WorkerStatus.ONLINE,
            capabilities=["llm.inference"],
            gpu_count=1,
            loaded_model="Qwen/Qwen3.5-9B",
            model_alias="local-dev",
            model_version="revision",
        )
    )
    session.commit()


def test_inference_job_api_requires_auth_and_enforces_claim_lease(session, monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))

    def override_session():
        yield session

    app.dependency_overrides[dependencies.get_session] = override_session
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-service-token"}
    try:
        assert client.post("/api/inference/jobs/claim", json={"worker_id": "home-5090-01"}).status_code == 401
        assert client.post("/api/inference/jobs/claim", headers=headers, json={"worker_id": "home-5090-01"}).status_code == 204

        register_eligible_worker(session)
        job = create_job(session)
        session.commit()
        claim = client.post("/api/inference/jobs/claim", headers=headers, json={"worker_id": "home-5090-01"})
        assert claim.status_code == 200
        claim_body = claim.json()
        assert claim_body["id"] == str(job.id)
        assert "claim_token" in claim_body

        wrong_worker = client.post(
            f"/api/inference/jobs/{job.id}/complete",
            headers=headers,
            json={
                "worker_id": "other-worker",
                "claim_token": claim_body["claim_token"],
                "result": {"structured_output": {"speech": "bad"}},
            },
        )
        assert wrong_worker.status_code == 409
        completed = client.post(
            f"/api/inference/jobs/{job.id}/complete",
            headers=headers,
            json={
                "worker_id": "home-5090-01",
                "claim_token": claim_body["claim_token"],
                "result": {"structured_output": {"speech": "done"}, "inference_ms": 1},
            },
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "SUCCEEDED"
    finally:
        app.dependency_overrides.clear()