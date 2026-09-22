import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic import SecretStr

from apps.control_api import dependencies
from apps.control_api.main import app, get_knowledge_service, get_provider_router
from packages.chat.service import ChatService
from packages.knowledge.embedding import HashEmbeddingProvider
from packages.knowledge.service import KnowledgeService
from packages.memory.service import MemoryService
from packages.persona.repository import PersonaRepository
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse, ChatRequest
from packages.schemas.knowledge import KnowledgeDocumentCreate
from packages.schemas.memory import MemoryCandidateCreate
from packages.schemas.persona import PersonaPackage


class CapturingProvider:
    name = "acceptance-api"
    model_id = "acceptance-model"

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def health(self) -> bool:
        return True

    async def generate(self, messages, response_schema: type[BaseModel], trace_id: str) -> BaseModel:
        self.messages = messages
        return AgentResponse(speech="Context received.")


@pytest.mark.asyncio
async def test_chat_combines_persona_matching_memory_and_knowledge(session) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(
            persona_id="inaba",
            version="inaba-2",
            display_name="Inaba",
            system_prompt="Unique persona instruction.",
        )
    )
    memory = MemoryService(session).create_candidate(
        MemoryCandidateCreate(
            type="preference",
            content="User prefers jasmine tea.",
            scope="shared",
            persona_id="inaba",
            subject_id="user-1",
            session_id="session-1",
            source_runtime_mode="human",
        )
    )
    MemoryService(session).confirm(memory.id)
    knowledge = KnowledgeService(session, HashEmbeddingProvider())
    knowledge.ingest(
        KnowledgeDocumentCreate(source_uri="knowledge/tea.md", content="# Tea\nJasmine tea is fragrant.")
    )
    provider = CapturingProvider()

    result = await ChatService(session, ProviderRouter(external=provider), knowledge).chat(
        ChatRequest(channel="web", session_id="session-1", user_id="user-1", text="Tell me about jasmine tea")
    )
    joined_prompt = "\n".join(message["content"] for message in provider.messages)

    assert result.response.speech == "Context received."
    assert "Unique persona instruction." in joined_prompt
    assert "User prefers jasmine tea." in joined_prompt
    assert "Jasmine tea is fragrant." in joined_prompt


def test_chat_endpoint_passes_persona_memory_and_knowledge_to_provider(session, monkeypatch) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(
            persona_id="inaba",
            version="inaba-api",
            display_name="Inaba",
            system_prompt="Endpoint persona instruction.",
        )
    )
    memory = MemoryService(session).create_candidate(
        MemoryCandidateCreate(
            type="preference",
            content="Endpoint memory.",
            scope="shared",
            persona_id="inaba",
            subject_id="api-user",
            session_id="api-session",
            source_runtime_mode="human",
        )
    )
    MemoryService(session).confirm(memory.id)
    knowledge = KnowledgeService(session, HashEmbeddingProvider())
    knowledge.ingest(
        KnowledgeDocumentCreate(source_uri="knowledge/endpoint.md", content="# Endpoint\nEndpoint knowledge.")
    )
    session.commit()
    provider = CapturingProvider()

    def override_session():
        yield session

    def override_router():
        return ProviderRouter(external=provider)

    def override_knowledge():
        return knowledge

    app.dependency_overrides = {
        dependencies.get_session: override_session,
        get_provider_router: override_router,
        get_knowledge_service: override_knowledge,
    }
    monkeypatch.setattr(dependencies.settings, "service_token", SecretStr("test-service-token"))
    try:
        response = TestClient(app).post(
            "/api/chat",
            json={"channel": "web", "session_id": "api-session", "user_id": "api-user", "text": "Endpoint"},
            headers={"Authorization": "Bearer test-service-token"},
        )
    finally:
        app.dependency_overrides.clear()

    prompt = "\n".join(message["content"] for message in provider.messages)
    assert response.status_code == 200
    assert "Endpoint persona instruction." in prompt
    assert "Endpoint memory." in prompt
    assert "Endpoint knowledge." in prompt