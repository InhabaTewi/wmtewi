from fastapi.testclient import TestClient
from pydantic import SecretStr

from apps.control_api import dependencies
from apps.control_api.main import app
from packages.persona.repository import PersonaRepository
from packages.schemas.persona import PersonaPackage


def test_active_persona_endpoint(session, monkeypatch) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(
            persona_id="inaba",
            version="inaba-1",
            display_name="Inaba",
            system_prompt="Stay in character.",
        )
    )
    session.commit()

    def override_session():
        yield session

    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))
    app.dependency_overrides[dependencies.get_session] = override_session
    try:
        response = TestClient(app).get(
            "/api/personas/inaba/active", headers={"Authorization": "Bearer test-service-token"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["version"] == "inaba-1"