import os
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
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

    ready = TestClient(app).get("/health/ready").json()
    assert ready["status"] == "ready"
    UUID(ready["check_id"])

    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: False)
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_reconstructed_readiness_diagnostics_correlate_failure_without_secrets(monkeypatch, caplog) -> None:
    async def healthy():
        return "healthy"

    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: False)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_llm_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)

    with caplog.at_level("WARNING", logger="apps.control_api.dependencies"):
        response = TestClient(app).get("/health/ready")

    payload = response.json()
    assert response.status_code == 503
    assert payload["failure_component"] == "database"
    UUID(payload["check_id"])
    assert f"check_id={payload['check_id']}" in caplog.text
    assert "failure_kind=unavailable" in caplog.text
    assert "database_ms=" in caplog.text
    assert "exception_types=none" in caplog.text


def test_reconstructed_readiness_diagnostics_redact_exception_messages(monkeypatch, caplog) -> None:
    secret = "postgresql://user:password@example/db?token=SECRET"

    def database_failure() -> bool:
        raise RuntimeError(secret)

    async def healthy():
        return "healthy"

    monkeypatch.setattr(dependencies, "check_database_readiness", database_failure)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_llm_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)

    with caplog.at_level("WARNING", logger="apps.control_api.dependencies"):
        response = TestClient(app).get("/health/ready")

    payload = response.json()
    assert response.status_code == 503
    assert payload["failure_component"] == "database"
    UUID(payload["check_id"])
    assert "exception_types=database:RuntimeError" in caplog.text
    assert secret not in response.text
    assert secret not in caplog.text


def test_reconstructed_llm_degraded_readiness_remains_http_200(monkeypatch) -> None:
    async def healthy():
        return "healthy"

    async def degraded():
        return "degraded"

    monkeypatch.setattr(dependencies, "check_database_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_llm_readiness", degraded)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    UUID(response.json()["check_id"])


class DatabaseConnection:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.scalar_calls = 0

    def scalar(self, _):
        self.scalar_calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class DatabaseEngine:
    def __init__(self, connection: DatabaseConnection | Exception) -> None:
        self.connection = connection
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        if isinstance(self.connection, Exception):
            raise self.connection
        return self.connection


def _healthy_readiness_dependencies(monkeypatch) -> None:
    async def healthy():
        return "healthy"

    monkeypatch.setattr(dependencies, "check_persona_readiness", lambda: True)
    monkeypatch.setattr(dependencies, "check_llm_readiness", healthy)
    monkeypatch.setattr(dependencies, "check_embedding_readiness", healthy)


@pytest.mark.parametrize(
    ("engine", "expected_stage", "expected_exception", "expected_reason"),
    [
        (
            DatabaseEngine(OperationalError("SELECT 1", {}, RuntimeError("connect failed"))),
            "acquire_connection",
            "OperationalError",
            "exception",
        ),
        (
            DatabaseEngine(DatabaseConnection([OperationalError("SELECT 1", {}, RuntimeError("query failed"))])),
            "select_one",
            "OperationalError",
            "exception",
        ),
        (
            DatabaseEngine(DatabaseConnection([1, OperationalError("SELECT 1", {}, RuntimeError("extension failed"))])),
            "pgvector_check",
            "OperationalError",
            "exception",
        ),
        (
            DatabaseEngine(DatabaseConnection([1, 1, OperationalError("SELECT 1", {}, RuntimeError("revision failed"))])),
            "alembic_revision_check",
            "OperationalError",
            "exception",
        ),
    ],
)
def test_database_readiness_exception_diagnostics_preserve_stage_and_check_id(
    monkeypatch, caplog, engine, expected_stage, expected_exception, expected_reason
) -> None:
    _healthy_readiness_dependencies(monkeypatch)
    monkeypatch.setattr(dependencies, "engine", engine)

    with caplog.at_level("WARNING", logger="apps.control_api.dependencies"):
        response = TestClient(app).get("/health/ready")

    payload = response.json()
    assert response.status_code == 503
    assert payload["database"] == "unavailable"
    assert payload["failure_component"] == "database"
    assert engine.connect_calls == 1
    assert f"event=db_readiness_failure check_id={payload['check_id']}" in caplog.text
    assert f"failure_stage={expected_stage}" in caplog.text
    assert f"failure_reason_code={expected_reason}" in caplog.text
    assert f"exception_class={expected_exception}" in caplog.text
    assert "exception_family=OperationalError" in caplog.text
    assert "connection_invalidated=false" in caplog.text


@pytest.mark.parametrize(
    ("responses", "expected_stage", "expected_reason"),
    [
        ([None], "select_one", "invalid_select_result"),
        ([1, None], "pgvector_check", "pgvector_not_ready"),
        ([1, 1, "outdated-revision"], "alembic_revision_check", "revision_mismatch"),
    ],
)
def test_database_readiness_validation_diagnostics_preserve_stage_without_exception(
    monkeypatch, caplog, responses, expected_stage, expected_reason
) -> None:
    _healthy_readiness_dependencies(monkeypatch)
    engine = DatabaseEngine(DatabaseConnection(responses))
    monkeypatch.setattr(dependencies, "engine", engine)
    monkeypatch.setattr(dependencies, "_expected_alembic_revision", lambda: "current-revision")

    with caplog.at_level("WARNING", logger="apps.control_api.dependencies"):
        response = TestClient(app).get("/health/ready")

    payload = response.json()
    assert response.status_code == 503
    assert engine.connect_calls == 1
    assert engine.connection.scalar_calls == len(responses)
    assert f"check_id={payload['check_id']}" in caplog.text
    assert f"failure_stage={expected_stage}" in caplog.text
    assert f"failure_reason_code={expected_reason}" in caplog.text
    assert "exception_class=NONE" in caplog.text


def test_database_readiness_diagnostic_logs_redact_exception_messages(monkeypatch, caplog) -> None:
    secret = "postgresql://user:SUPER_SECRET@db/internal password=SUPER_SECRET"
    _healthy_readiness_dependencies(monkeypatch)
    monkeypatch.setattr(
        dependencies,
        "engine",
        DatabaseEngine(OperationalError("SELECT 1", {}, RuntimeError(secret))),
    )

    with caplog.at_level("WARNING", logger="apps.control_api.dependencies"):
        response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert "exception_class=OperationalError" in caplog.text
    assert "SUPER_SECRET" not in response.text
    assert "SUPER_SECRET" not in caplog.text
    assert "postgresql://" not in caplog.text
    assert "password=" not in caplog.text


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

        def __exit__(self, *_):
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
    payload = response.json()
    UUID(payload.pop("check_id"))
    assert payload == {
        "status": "ready",
        "database": "ok",
        "persona": "ok",
        "llm": "healthy",
        "embedding": "healthy",
    }