import os
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.control_api import dependencies
from apps.control_api.main import app
from packages.persona.repository import PersonaRepository
from packages.providers.external_openai import ExternalOpenAIProvider
from packages.providers.router import ProviderRouter
from packages.schemas.persona import PersonaPackage


def test_live_and_compatibility_health_are_anonymous(monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))
    client = TestClient(app)

    for path in ("/health", "/health/live"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "inaba-core"}


def test_live_health_does_not_depend_on_readiness(monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "readiness_report", lambda: (_ for _ in ()).throw(AssertionError()))

    assert TestClient(app).get("/health/live").status_code == 200


def test_unknown_path_returns_not_found_before_authentication() -> None:
    response = TestClient(app).get("/tewi/health/live")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/chat", {}),
        ("/api/memory/search", {}),
        ("/api/knowledge/documents", {}),
        ("/api/workers/register", {}),
    ],
)
def test_business_routes_require_a_valid_bearer_token(monkeypatch, path: str, payload: dict) -> None:
    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))
    client = TestClient(app)

    for headers in ({}, {"Authorization": "Basic test-service-token"}, {"Authorization": "Bearer wrong-token"}):
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 401
        assert response.json() == {"detail": "Unauthorized"}


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/chat", {}),
        ("/api/memory/search", {}),
        ("/api/knowledge/documents", {}),
        ("/api/workers/register", {}),
    ],
)
def test_correct_bearer_token_reaches_business_routes(monkeypatch, path: str, payload: dict) -> None:
    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))

    response = TestClient(app, raise_server_exceptions=False).post(
        path, headers={"Authorization": "Bearer test-service-token"}, json=payload
    )

    assert response.status_code != 401


def test_health_ready_is_anonymous_and_redacts_failures(monkeypatch) -> None:
    async def unavailable():
        return {
            "status": "not_ready",
            "database": "unavailable",
            "persona": "unavailable",
            "llm": "unavailable",
            "embedding": "unavailable",
        }

    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("super-secret-token"))
    monkeypatch.setattr(dependencies, "readiness_report", unavailable)

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "super-secret-token" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_kind"),
    [(401, "authentication"), (403, "authentication"), (429, "rate_limit"), (500, "unavailable"), (502, "unavailable"), (503, "unavailable")],
)
async def test_llm_readiness_logs_safe_http_failure_diagnostics(monkeypatch, caplog, status_code, expected_kind) -> None:
    provider = ExternalOpenAIProvider(
        base_url="https://user:password@example.invalid/v1",
        api_key="SUPER_SECRET",
        model_id="test-model",
        max_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code)),
    )
    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(external=provider, mode="cloud"),
    )
    with caplog.at_level(logging.WARNING, logger="apps.control_api.dependencies"):
        assert await dependencies.check_llm_readiness() == "unavailable"

    message = caplog.messages[-1]
    assert "event=llm_readiness_failure" in message
    assert f"failure_kind={expected_kind}" in message
    assert f"http_status={status_code}" in message
    assert "SUPER_SECRET" not in message
    assert "user:password" not in message


@pytest.mark.asyncio
async def test_llm_readiness_logs_timeout_and_connection_without_secret_leakage(monkeypatch, caplog) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("token=SUPER_SECRET https://user:password@example.invalid")

    provider = ExternalOpenAIProvider(
        base_url="https://user:password@example.invalid/v1",
        api_key="SUPER_SECRET",
        model_id="test-model",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(external=provider, mode="cloud"),
    )
    with caplog.at_level(logging.WARNING, logger="apps.control_api.dependencies"):
        assert await dependencies.check_llm_readiness() == "unavailable"

    message = caplog.messages[-1]
    assert "failure_kind=timeout" in message
    assert "exception_class=ConnectTimeout" in message
    assert "SUPER_SECRET" not in message
    assert "user:password" not in message


@pytest.mark.asyncio
async def test_llm_readiness_logs_connection_failure_without_secret_leakage(monkeypatch, caplog) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("token=SUPER_SECRET https://user:password@example.invalid")

    provider = ExternalOpenAIProvider(
        base_url="https://user:password@example.invalid/v1",
        api_key="SUPER_SECRET",
        model_id="test-model",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(external=provider, mode="cloud"),
    )
    with caplog.at_level(logging.WARNING, logger="apps.control_api.dependencies"):
        assert await dependencies.check_llm_readiness() == "unavailable"

    message = caplog.messages[-1]
    assert "failure_kind=connection" in message
    assert "exception_class=ConnectError" in message
    assert "SUPER_SECRET" not in message
    assert "user:password" not in message


def test_models_not_found_preserves_cloud_ready_http_200_with_one_probe(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, content=b"not json")

    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", lambda: asyncio_sleep_healthy())

    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(external=provider, mode="cloud"),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["llm"] == "healthy"
    assert response.json()["check_id"]
    assert calls == 1


@pytest.mark.asyncio
async def test_successful_models_probe_does_not_parse_response_body() -> None:
    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json")),
    )

    assert (await provider.health_status()).state == "healthy"
    assert provider.last_health_failure is None
    await provider.aclose()


def test_ready_response_and_llm_failure_log_share_check_id(monkeypatch, caplog) -> None:
    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        max_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", lambda: asyncio_sleep_healthy())
    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(external=provider, mode="cloud"),
    )
    with caplog.at_level(logging.WARNING, logger="apps.control_api.dependencies"):
        response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    check_id = response.json()["check_id"]
    assert check_id
    assert f"check_id={check_id}" in caplog.messages[-1]


async def asyncio_sleep_healthy() -> str:
    return "healthy"


@pytest.mark.asyncio
async def test_local_worker_readiness_does_not_require_external_provider(monkeypatch) -> None:
    class LocalProvider:
        async def health(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(external=None, local=LocalProvider(), mode="local_worker"),
    )

    assert await dependencies.check_llm_readiness() == "healthy"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("local_healthy", "cloud_healthy", "expected"),
    [(True, True, "healthy"), (True, False, "degraded"), (False, True, "degraded"), (False, False, "unavailable")],
)
async def test_prefer_local_readiness_accepts_either_provider(monkeypatch, local_healthy, cloud_healthy, expected) -> None:
    class Provider:
        def __init__(self, healthy: bool) -> None:
            self.healthy = healthy

        async def health(self) -> bool:
            return self.healthy

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        dependencies,
        "create_provider_router",
        lambda: ProviderRouter(
            external=Provider(cloud_healthy),
            local=Provider(local_healthy),
            mode="prefer_local_with_cloud_fallback",
        ),
    )

    assert await dependencies.check_llm_readiness() == expected


def test_readiness_state_requires_database_persona_and_all_providers(monkeypatch) -> None:
    async def healthy():
        return "healthy"

    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_llm_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)

    assert TestClient(app).get("/health/ready").json()["status"] == "ready"

    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: False)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


@pytest.mark.parametrize("failed_check", ["persona", "llm", "embedding"])
def test_readiness_marks_required_dependency_unavailable(monkeypatch, failed_check: str) -> None:
    async def healthy():
        return "healthy"

    async def unavailable():
        return "unavailable"

    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_llm_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)
    if failed_check == "persona":
        monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: False)
    elif failed_check == "llm":
        monkeypatch.setattr(dependencies, "check_llm_readiness", unavailable)
    else:
        monkeypatch.setattr(dependencies, "check_embedding_readiness", unavailable)

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json()[failed_check] == "unavailable"


@pytest.mark.parametrize(
    ("extension_exists", "database_revision"),
    [(False, "20260922_0004"), (True, "outdated-revision")],
)
def test_database_readiness_requires_pgvector_and_current_revision(
    monkeypatch, extension_exists: bool, database_revision: str
) -> None:
    class Connection:
        def scalar(self, statement):
            query = str(statement)
            if "SELECT 1" == query:
                return 1
            if "pg_extension" in query:
                return 1 if extension_exists else None
            return database_revision

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(dependencies, "engine", SimpleNamespace(connect=Connection))
    monkeypatch.setattr(dependencies, "_expected_alembic_revision", lambda: "20260922_0004")

    assert dependencies.check_database_readiness() is False


def test_ready_endpoint_checks_real_postgresql_state(monkeypatch) -> None:
    postgres_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not postgres_url:
        pytest.skip("set POSTGRES_INTEGRATION_URL to run PostgreSQL readiness integration tests")
    engine = create_engine(postgres_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as session:
        PersonaRepository(session).upsert_version(
            PersonaPackage(
                persona_id="inaba",
                version="readiness-test",
                display_name="Inaba",
                system_prompt="Readiness persona.",
            )
        )
        session.commit()

    async def healthy():
        return "healthy"

    monkeypatch.setattr(dependencies, "engine", engine)
    monkeypatch.setattr(dependencies, "SessionLocal", session_factory)
    monkeypatch.setattr(dependencies, "check_llm_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "persona": "ok",
        "llm": "healthy",
        "embedding": "healthy",
    }