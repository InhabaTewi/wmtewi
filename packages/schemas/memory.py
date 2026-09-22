from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MemoryType = Literal[
    "preference",
    "stable_fact",
    "relationship",
    "promise",
    "state_change",
    "plot_point",
    "episodic_summary",
]


class MemoryAtom(BaseModel):
    id: UUID
    type: MemoryType
    subject_id: str | None = None
    object_id: str | None = None
    persona_id: str | None = None
    session_id: str | None = None
    content: str
    scope: str
    importance: float
    confidence: float
    confirmed: bool
    source_event_id: UUID | None = None
    source_runtime_mode: Literal["local", "api", "import", "human"]
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    version: int = 1
    supersedes_id: UUID | None = None


class MemoryCandidate(BaseModel):
    type: MemoryType
    content: str
    scope: str
    importance: float = 0.5
    confidence: float = 0.5
    subject_id: str | None = None
    object_id: str | None = None
    persona_id: str | None = None
    session_id: str | None = None


class MemoryCandidateCreate(MemoryCandidate):
    source_event_id: UUID | None = None
    source_runtime_mode: Literal["local", "api", "import", "human"]


class MemorySearchRequest(BaseModel):
    scope: str | None = None
    subject_id: str | None = None
    persona_id: str | None = None
    session_id: str | None = None
    query: str | None = None
    include_unconfirmed: bool = False
    limit: int = Field(default=20, ge=1, le=100)


class MemorySupersedeRequest(MemoryCandidateCreate):
    pass


class MemoryImportAtom(MemoryAtom):
    """Portable JSONL record for a stable-ID Memory migration."""

    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    updated_at: datetime

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if not value.strip() or len(value) > 256:
            raise ValueError("scope must be non-empty and at most 256 characters")
        return value

    @field_validator("persona_id")
    @classmethod
    def validate_persona_id(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value) > 128):
            raise ValueError("persona_id must be non-empty and at most 128 characters")
        return value

    @field_validator("importance", "confidence")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("importance and confidence must be between 0 and 1")
        return value
