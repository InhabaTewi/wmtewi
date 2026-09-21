from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.memory.service import MemoryService
from packages.persistence.models import Base
from packages.schemas.memory import MemoryCandidateCreate, MemorySearchRequest


def _confirmed_memory(service: MemoryService, *, persona_id: str, user_id: str, session_id: str, content: str) -> None:
    memory = service.create_candidate(
        MemoryCandidateCreate(
            type="preference",
            content=content,
            scope="shared",
            persona_id=persona_id,
            subject_id=user_id,
            session_id=session_id,
            source_runtime_mode="human",
        )
    )
    service.confirm(memory.id)


def test_memory_persists_when_database_is_reopened(tmp_path) -> None:
    database_path = tmp_path / "acceptance.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as first_process:
        service = MemoryService(first_process)
        _confirmed_memory(
            service, persona_id="inaba", user_id="user-1", session_id="session-1", content="prefers tea"
        )
        first_process.commit()

    engine.dispose()
    restarted_engine = create_engine(f"sqlite:///{database_path}")
    restarted_sessions = sessionmaker(bind=restarted_engine)
    with restarted_sessions() as restarted_process:
        memories = MemoryService(restarted_process).search(
            MemorySearchRequest(persona_id="inaba", subject_id="user-1", session_id="session-1")
        )

    assert [memory.content for memory in memories] == ["prefers tea"]


def test_memory_isolated_by_persona_user_and_session(session) -> None:
    service = MemoryService(session)
    _confirmed_memory(service, persona_id="inaba", user_id="user-1", session_id="session-1", content="visible")
    _confirmed_memory(service, persona_id="other", user_id="user-1", session_id="session-1", content="other persona")
    _confirmed_memory(service, persona_id="inaba", user_id="user-2", session_id="session-1", content="other user")
    _confirmed_memory(service, persona_id="inaba", user_id="user-1", session_id="session-2", content="other session")
    session.commit()

    memories = service.by_context(persona_id="inaba", subject_id="user-1", session_id="session-1")

    assert [memory.content for memory in memories] == ["visible"]