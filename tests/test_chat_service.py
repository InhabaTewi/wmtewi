import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from packages.chat.service import ChatService
from packages.persona.repository import PersonaRepository
from packages.persistence.models import InteractionTrace, Message, SessionRecord
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse, ChatRequest
from packages.schemas.memory import MemoryCandidate
from packages.schemas.persona import PersonaPackage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_URL_ENV = "POSTGRES_INTEGRATION_URL"


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


@pytest.mark.asyncio
async def test_postgres_first_chat_persists_session_before_messages() -> None:
    postgres_url = os.environ.get(INTEGRATION_URL_ENV)
    if not postgres_url:
        pytest.skip(f"set {INTEGRATION_URL_ENV} to run PostgreSQL chat persistence integration tests")
    if not postgres_url.startswith("postgresql+") or "test" not in postgres_url.lower():
        pytest.fail(f"{INTEGRATION_URL_ENV} must target a dedicated PostgreSQL test database")

    environment = os.environ | {"APP_ENV": "test", "DATABASE_URL": postgres_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, env=environment, check=True)
    engine = create_engine(postgres_url)
    session = sessionmaker(bind=engine)()
    session_id = f"postgres-chat-{uuid4()}"
    persona_id = f"postgres-persona-{uuid4()}"
    try:
        PersonaRepository(session).upsert_version(
            PersonaPackage(
                persona_id=persona_id,
                version="v1",
                display_name="PostgreSQL Chat Test",
                system_prompt="Persist the first chat session before its messages.",
            )
        )
        result = await ChatService(session, ProviderRouter(external=FakeProvider())).chat(
            ChatRequest(
                persona_id=persona_id,
                channel="web",
                session_id=session_id,
                user_id="postgres-chat-user",
                text="Persist this first chat.",
            )
        )

        assert session.get(SessionRecord, session_id) is not None
        assert len(session.scalars(select(Message).where(Message.session_id == session_id)).all()) == 2
        assert session.get(InteractionTrace, result.trace_id) is not None
    finally:
        session.rollback()
        session.close()
        engine.dispose()