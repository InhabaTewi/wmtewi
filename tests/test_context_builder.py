from datetime import UTC, datetime
from uuid import uuid4

from packages.context_builder.builder import ContextBuilder, SAFETY_TOOL_POLICY
from packages.persona.repository import PersonaRepository
from packages.schemas.chat import ChatEvent
from packages.schemas.persona import PersonaPackage


def test_context_messages_follow_required_order(session) -> None:
    PersonaRepository(session).upsert_version(
        PersonaPackage(
            persona_id="inaba",
            version="inaba-1",
            display_name="Inaba",
            system_prompt="Persona instructions.",
        )
    )
    event = ChatEvent(
        event_id=uuid4(),
        trace_id=uuid4(),
        channel="web",
        session_id="session-1",
        user_id="user-1",
        text="Hello",
        timestamp=datetime.now(UTC),
    )

    messages = ContextBuilder.to_messages(ContextBuilder(session).build(event, "api"), event)

    assert messages[0]["role"] == "system"
    assert "Persona instructions." in messages[0]["content"]
    assert SAFETY_TOOL_POLICY in messages[0]["content"]
    assert sum(message["role"] == "system" for message in messages) == 1
    assert messages[-1] == {"role": "user", "content": "Hello"}