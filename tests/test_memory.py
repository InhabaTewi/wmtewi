from packages.memory.service import MemoryService
from packages.schemas.memory import MemoryCandidateCreate, MemorySearchRequest, MemorySupersedeRequest


def test_confirmed_memory_is_shared_across_runtime_modes(session) -> None:
    service = MemoryService(session)
    candidate = service.create_candidate(
        MemoryCandidateCreate(
            type="preference",
            content="prefers tea",
            scope="shared",
            subject_id="user-1",
            source_runtime_mode="local",
            confidence=0.9,
        )
    )
    service.confirm(candidate.id)
    session.commit()

    memories = service.search(MemorySearchRequest(scope="shared", subject_id="user-1"))

    assert [memory.content for memory in memories] == ["prefers tea"]
    assert memories[0].source_runtime_mode == "local"


def test_memory_candidate_is_deduplicated_and_superseded(session) -> None:
    service = MemoryService(session)
    candidate_input = MemoryCandidateCreate(
        type="stable_fact",
        content="lives in Kyoto",
        scope="shared",
        subject_id="user-1",
        source_runtime_mode="api",
    )
    first = service.create_candidate(candidate_input)
    duplicate = service.create_candidate(candidate_input)
    replacement = service.supersede(
        first.id,
        MemorySupersedeRequest(
            type="stable_fact",
            content="lives in Tokyo",
            scope="shared",
            subject_id="user-1",
            source_runtime_mode="human",
        ),
    )

    assert duplicate.id == first.id
    assert replacement.supersedes_id == first.id
    assert replacement.version == 2