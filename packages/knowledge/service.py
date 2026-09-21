import hashlib
import math
import re
from uuid import UUID

from sqlalchemy.orm import Session

from packages.knowledge.embedding import EmbeddingProvider
from packages.knowledge.repository import KnowledgeRepository
from packages.persistence.models import KnowledgeChunk as KnowledgeChunkRecord
from packages.persistence.models import KnowledgeDocument
from packages.schemas.chat import KnowledgeChunk
from packages.schemas.knowledge import KnowledgeDocumentCreate, KnowledgeDocumentRead


class KnowledgeNotFoundError(Exception):
    pass


class KnowledgeService:
    def __init__(self, session: Session, embedding_provider: EmbeddingProvider) -> None:
        self.session = session
        self.embedding_provider = embedding_provider
        self.repository = KnowledgeRepository(session)

    def ingest(self, request: KnowledgeDocumentCreate, force_reindex: bool = False) -> KnowledgeDocumentRead:
        content = self._normalize(request.content)
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        current = self.repository.active_by_source(request.source_uri)
        if current is not None and current.sha256 == sha256 and not force_reindex:
            return self._to_document_dto(current)
        version = (current.version if current is not None else 0) + 1
        self.repository.deactivate_source(request.source_uri)
        document = self.repository.add_document(
            KnowledgeDocument(
                source_uri=request.source_uri,
                sha256=sha256,
                version=version,
                content=content,
                is_active=True,
            )
        )
        chunks = self._split(content)
        embeddings = self.embedding_provider.embed(chunks)
        for ordinal, (chunk_content, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            self.repository.add_chunk(
                KnowledgeChunkRecord(document_id=document.id, ordinal=ordinal, content=chunk_content),
                embedding,
                self.embedding_provider.model_id,
            )
        self.session.flush()
        return self._to_document_dto(document)

    def reindex(self, document_id: UUID) -> KnowledgeDocumentRead:
        document = self.repository.get_active(document_id)
        if document is None:
            raise KnowledgeNotFoundError(str(document_id))
        return self.ingest(
            KnowledgeDocumentCreate(source_uri=document.source_uri, content=document.content),
            force_reindex=True,
        )

    def search(self, query: str, limit: int = 5) -> list[KnowledgeChunk]:
        query_vector = self.embedding_provider.embed([query])[0]
        scored: list[KnowledgeChunk] = []
        for chunk, embedding in self.repository.active_chunk_embeddings():
            score = self._cosine(query_vector, embedding.embedding)
            scored.append(
                KnowledgeChunk(id=chunk.id, document_id=chunk.document_id, content=chunk.content, score=score)
            )
        return sorted(scored, key=lambda item: item.score or 0, reverse=True)[:limit]

    def delete(self, document_id: UUID) -> None:
        document = self.repository.get_active(document_id)
        if document is None:
            raise KnowledgeNotFoundError(str(document_id))
        document.is_active = False
        self.session.flush()

    @staticmethod
    def _normalize(content: str) -> str:
        return re.sub(r"\n{3,}", "\n\n", content.replace("\r\n", "\n").strip())

    @staticmethod
    def _split(content: str, max_length: int = 1600) -> list[str]:
        sections: list[str] = []
        current: list[str] = []
        for line in content.splitlines():
            if line.startswith("#") and current:
                sections.append("\n".join(current).strip())
                current = []
            current.append(line)
        if current:
            sections.append("\n".join(current).strip())
        return [section[index : index + max_length] for section in sections for index in range(0, len(section), max_length)]

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        left_magnitude = math.sqrt(sum(value * value for value in left))
        right_magnitude = math.sqrt(sum(value * value for value in right))
        return numerator / (left_magnitude * right_magnitude or 1.0)

    @staticmethod
    def _to_document_dto(document: KnowledgeDocument) -> KnowledgeDocumentRead:
        return KnowledgeDocumentRead(
            id=document.id,
            source_uri=document.source_uri,
            sha256=document.sha256,
            version=document.version,
            is_active=document.is_active,
        )