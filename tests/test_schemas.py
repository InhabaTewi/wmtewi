from datetime import UTC, datetime
from uuid import uuid4

from packages.schemas import ChatEvent


def test_chat_event_has_independent_metadata_defaults() -> None:
    first = ChatEvent(
        event_id=uuid4(), trace_id=uuid4(), channel="qq", session_id="s1", user_id="u1", text="hi", timestamp=datetime.now(UTC)
    )
    second = ChatEvent(
        event_id=uuid4(), trace_id=uuid4(), channel="web", session_id="s2", user_id="u2", text="hello", timestamp=datetime.now(UTC)
    )
    first.metadata["key"] = "value"
    assert second.metadata == {}
