from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from packages.persistence.models import MemoryAtomRecord


class MemoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, memory_id: UUID) -> MemoryAtomRecord | None:
        return self.session.get(MemoryAtomRecord, memory_id)

    def find_duplicate(
        self, *, content: str, scope: str, subject_id: str | None
    ) -> MemoryAtomRecord | None:
        statement = select(MemoryAtomRecord).where(
            MemoryAtomRecord.content == content,
            MemoryAtomRecord.scope == scope,
            MemoryAtomRecord.subject_id == subject_id,
            MemoryAtomRecord.valid_to.is_(None),
        )
        return self.session.scalar(statement)

    def search(
        self,
        *,
        scope: str | None,
        subject_id: str | None,
        persona_id: str | None,
        session_id: str | None,
        query: str | None,
        include_unconfirmed: bool,
        limit: int,
    ) -> list[MemoryAtomRecord]:
        statement: Select[tuple[MemoryAtomRecord]] = select(MemoryAtomRecord).where(
            MemoryAtomRecord.valid_to.is_(None)
        )
        if not include_unconfirmed:
            statement = statement.where(MemoryAtomRecord.confirmed.is_(True))
        if scope is not None:
            statement = statement.where(MemoryAtomRecord.scope == scope)
        if subject_id is not None:
            statement = statement.where(MemoryAtomRecord.subject_id == subject_id)
        if persona_id is not None:
            statement = statement.where(MemoryAtomRecord.persona_id == persona_id)
        if session_id is not None:
            statement = statement.where(MemoryAtomRecord.session_id == session_id)
        if query:
            statement = statement.where(MemoryAtomRecord.content.ilike(f"%{query}%"))
        statement = statement.order_by(MemoryAtomRecord.importance.desc()).limit(limit)
        return list(self.session.scalars(statement))

    def add(self, record: MemoryAtomRecord) -> MemoryAtomRecord:
        self.session.add(record)
        self.session.flush()
        return record

    def export_records(
        self,
        *,
        persona_id: str | None,
        subject_id: str | None,
        confirmed_only: bool,
        active_only: bool,
    ) -> list[MemoryAtomRecord]:
        statement: Select[tuple[MemoryAtomRecord]] = select(MemoryAtomRecord)
        if persona_id is not None:
            statement = statement.where(MemoryAtomRecord.persona_id == persona_id)
        if subject_id is not None:
            statement = statement.where(MemoryAtomRecord.subject_id == subject_id)
        if confirmed_only:
            statement = statement.where(MemoryAtomRecord.confirmed.is_(True))
        if active_only:
            statement = statement.where(MemoryAtomRecord.valid_to.is_(None))
        statement = statement.order_by(MemoryAtomRecord.created_at, MemoryAtomRecord.id)
        return list(self.session.scalars(statement))