from packages.knowledge.embedding import HashEmbeddingProvider
import pytest

from packages.knowledge.service import KnowledgeService
from packages.schemas.knowledge import KnowledgeDocumentCreate


def test_updated_document_replaces_active_knowledge_version(session) -> None:
    service = KnowledgeService(session, HashEmbeddingProvider())
    first = service.ingest(
        KnowledgeDocumentCreate(source_uri="knowledge/world.md", content="# World\nOld capital is Kyoto.")
    )
    second = service.ingest(
        KnowledgeDocumentCreate(source_uri="knowledge/world.md", content="# World\nNew capital is Tokyo.")
    )
    session.commit()

    chunks = service.search("Tokyo")

    assert second.version == first.version + 1
    assert len(chunks) == 1
    assert "Tokyo" in chunks[0].content


def test_reindex_creates_a_new_document_version(session) -> None:
    service = KnowledgeService(session, HashEmbeddingProvider())
    document = service.ingest(
        KnowledgeDocumentCreate(source_uri="knowledge/faq.md", content="# FAQ\nUse Memory Service.")
    )

    reindexed = service.reindex(document.id)

    assert reindexed.version == document.version + 1


@pytest.mark.parametrize("limit", [0, -1, 21])
def test_search_rejects_invalid_top_k(session, limit: int) -> None:
    service = KnowledgeService(session, HashEmbeddingProvider())

    with pytest.raises(ValueError, match="limit must be between 1 and 20"):
        service.search("query", limit)