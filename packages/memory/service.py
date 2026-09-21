from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from packages.memory.repository import MemoryRepository
from packages.persistence.models import MemoryAtomRecord
from packages.schemas.memory import (
    MemoryAtom,
    MemoryCandidateCreate,
    MemorySearchRequest,
    MemorySupersedeRequest,
)


class MemoryNotFoundError(Exception):
    pass


class MemoryService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = MemoryRepository(session)

    def search(self, request: MemorySearchRequest) -> list[MemoryAtom]:
        return [self._to_dto(record) for record in self.repository.search(**request.model_dump())]

    def create_candidate(self, candidate: MemoryCandidateCreate) -> MemoryAtom:
        duplicate = self.repository.find_duplicate(
            content=candidate.content,
            scope=candidate.scope,
            subject_id=candidate.subject_id,
        )
        if duplicate is not None:
            return self._to_dto(duplicate)

        record = MemoryAtomRecord(
            type=candidate.type,
            subject_id=candidate.subject_id,
            object_id=candidate.object_id,
            persona_id=candidate.persona_id,
            session_id=candidate.session_id,
            content=candidate.content,
            scope=candidate.scope,
            importance=candidate.importance,
            confidence=candidate.confidence,
            confirmed=False,
            source_event_id=candidate.source_event_id,
            source_runtime_mode=candidate.source_runtime_mode,
        )
        return self._to_dto(self.repository.add(record))

    def confirm(self, memory_id: UUID) -> MemoryAtom:
        record = self._require(memory_id)
        record.confirmed = True
        self.session.flush()
        return self._to_dto(record)

    def supersede(self, memory_id: UUID, replacement: MemorySupersedeRequest) -> MemoryAtom:
        previous = self._require(memory_id)
        previous.valid_to = datetime.now(UTC)
        record = MemoryAtomRecord(
            type=replacement.type,
            subject_id=replacement.subject_id,
            object_id=replacement.object_id,
            persona_id=replacement.persona_id,
            session_id=replacement.session_id,
            content=replacement.content,
            scope=replacement.scope,
            importance=replacement.importance,
            confidence=replacement.confidence,
            confirmed=False,
            source_event_id=replacement.source_event_id,
            source_runtime_mode=replacement.source_runtime_mode,
            version=previous.version + 1,
            supersedes_id=previous.id,
        )
        return self._to_dto(self.repository.add(record))

    def by_context(self, *, persona_id: str, subject_id: str, session_id: str) -> list[MemoryAtom]:
        return self.search(
            MemorySearchRequest(
                persona_id=persona_id,
                subject_id=subject_id,
                session_id=session_id,
            )
        )

    def _require(self, memory_id: UUID) -> MemoryAtomRecord:
        record = self.repository.get(memory_id)
        if record is None:
            raise MemoryNotFoundError(str(memory_id))
        return record

    @staticmethod
    def _to_dto(record: MemoryAtomRecord) -> MemoryAtom:
        return MemoryAtom(
            id=record.id,
            type=record.type,
            subject_id=record.subject_id,
            object_id=record.object_id,
            persona_id=record.persona_id,
            session_id=record.session_id,
            content=record.content,
            scope=record.scope,
            importance=record.importance,
            confidence=record.confidence,
            confirmed=record.confirmed,
            source_event_id=record.source_event_id,
            source_runtime_mode=record.source_runtime_mode,
            valid_from=record.valid_from,
            valid_to=record.valid_to,
            version=record.version,
            supersedes_id=record.supersedes_id,
        )