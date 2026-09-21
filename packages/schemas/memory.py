from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

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
