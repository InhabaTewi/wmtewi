import pytest
from pydantic import BaseModel

from packages.chat.service import ChatService
from packages.persona.repository import PersonaRepository
from packages.persistence.models import InteractionTrace
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse, ChatRequest
from packages.schemas.memory import MemoryCandidate
from packages.schemas.persona import PersonaPackage


class FakeProvider:
    name = "fake-api"
    model_id = "fake-model"

    async def health(self) -> bool:
        return True

    async def generate(self, messages, response_schema: type[BaseModel], trace_id: str) -> BaseModel:
        return AgentResponse(
            speech="Hello.",
            memory_candidates=[MemoryCandidate(type="preference", content="likes tea", scope="shared")],
        )


@pytest.mark.asyncio
async def test_chat_persists_trace_and_routes_memory_candidates_through_service(session) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(
            persona_id="inaba",
            version="inaba-1",
            display_name="Inaba",
            system_prompt="Stay in character.",
        )
    )
    result = await ChatService(session, ProviderRouter(external=FakeProvider())).chat(
        ChatRequest(channel="web", session_id="s-1", user_id="u-1", text="Hi")
    )

    trace = session.get(InteractionTrace, result.trace_id)

    assert result.runtime_mode == "api"
    assert trace is not None
    assert trace.payload["provider"] == "fake-api"
    assert trace.payload["response"]["speech"] == "Hello."