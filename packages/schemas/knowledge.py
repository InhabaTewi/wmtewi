from uuid import UUID

from pydantic import BaseModel, Field

from packages.schemas.chat import KnowledgeChunk


class KnowledgeDocumentCreate(BaseModel):
    source_uri: str
    content: str = Field(min_length=1)


class KnowledgeDocumentRead(BaseModel):
    id: UUID
    source_uri: str
    sha256: str
    version: int
    is_active: bool


class KnowledgeSearchResponse(BaseModel):
    chunks: list[KnowledgeChunk]