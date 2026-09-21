from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from packages.schemas.memory import MemoryAtom, MemoryCandidate


class KnowledgeChunk(BaseModel):
    id: UUID
    document_id: UUID
    content: str
    score: float | None = None


class BehaviorExample(BaseModel):
    id: UUID
    input_text: str
    output_text: str


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatEvent(BaseModel):
    event_id: UUID
    trace_id: UUID
    channel: Literal["qq", "web", "bilibili", "voice"]
    session_id: str
    user_id: str
    text: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    persona_id: str = "inaba"
    channel: Literal["qq", "web", "bilibili", "voice"]
    session_id: str
    user_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResult(BaseModel):
    trace_id: UUID
    runtime_mode: Literal["local", "api"]
    provider: str
    response: "AgentResponse"


class AgentContext(BaseModel):
    persona_version: str
    persona_system_prompt: str
    recent_messages: list[dict[str, Any]]
    memories: list[MemoryAtom]
    knowledge_chunks: list[KnowledgeChunk]
    behavior_examples: list[BehaviorExample]
    runtime_mode: Literal["local", "api"]


class AgentResponse(BaseModel):
    speech: str
    emotion: str | None = None
    expression: str | None = None
    actions: list[ToolCall] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
