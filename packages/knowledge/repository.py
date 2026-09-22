from uuid import UUID

import math

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from packages.persistence.models import KnowledgeChunk, KnowledgeDocument, KnowledgeEmbedding


class KnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def active_by_source(self, source_uri: str) -> KnowledgeDocument | None:
        return self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_uri == source_uri,
                KnowledgeDocument.is_active.is_(True),
            )
        )

    def get_active(self, document_id: UUID) -> KnowledgeDocument | None:
        return self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.is_active.is_(True),
            )
        )

    def list_active(self, source_uri: str | None = None) -> list[KnowledgeDocument]:
        statement = select(KnowledgeDocument).where(KnowledgeDocument.is_active.is_(True))
        if source_uri is not None:
            statement = statement.where(KnowledgeDocument.source_uri == source_uri)
        return list(self.session.scalars(statement.order_by(KnowledgeDocument.source_uri)))

    def deactivate_source(self, source_uri: str) -> None:
        self.session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.source_uri == source_uri)
            .values(is_active=False)
        )

    def add_document(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self.session.add(document)
        self.session.flush()
        return document

    def add_chunk(self, chunk: KnowledgeChunk, embedding: list[float], model_id: str) -> None:
        self.session.add(chunk)
        self.session.flush()
        self.session.add(
            KnowledgeEmbedding(chunk_id=chunk.id, embedding=embedding, embedding_model=model_id)
        )

    def active_chunk_embeddings(self) -> list[tuple[KnowledgeChunk, KnowledgeEmbedding]]:
        statement = (
            select(KnowledgeChunk, KnowledgeEmbedding)
            .join(KnowledgeEmbedding, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.is_active.is_(True))
        )
        return list(self.session.execute(statement).all())

    @property
    def supports_vector_search(self) -> bool:
        bind = self.session.get_bind()
        return bind.dialect.name == "postgresql"

    def search(self, query_embedding: list[float], model_id: str, limit: int) -> list[tuple[KnowledgeChunk, float]]:
        if len(query_embedding) != 1536 or not all(math.isfinite(value) for value in query_embedding):
            raise ValueError("query embedding must contain 1536 finite values")
        distance = KnowledgeEmbedding.embedding.cosine_distance(query_embedding).label("distance")
        statement = (
            select(KnowledgeChunk, distance)
            .join(KnowledgeEmbedding, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeDocument.is_active.is_(True),
                KnowledgeEmbedding.embedding_model == model_id,
            )
            .order_by(distance)
            .limit(limit)
        )
        return [(chunk, 1.0 - float(distance)) for chunk, distance in self.session.execute(statement)]