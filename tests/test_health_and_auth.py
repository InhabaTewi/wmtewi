import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.control_api import dependencies
from apps.control_api.main import app
from packages.persona.repository import PersonaRepository
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