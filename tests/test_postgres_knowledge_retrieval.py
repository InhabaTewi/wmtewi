import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from packages.knowledge.service import KnowledgeService
from packages.persistence.models import Base, KnowledgeChunk, KnowledgeDocument, KnowledgeEmbedding


INTEGRATION_URL_ENV = "POSTGRES_INTEGRATION_URL"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_A = "model-a"
MODEL_B = "model-b"


class FixedEmbeddingProvider:
    def __init__(self, vectors: dict[str, list[float]], model_id: str = MODEL_A) -> None:
        self.vectors = vectors
        self.model_id = model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors[text] for text in texts]

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)


def vector(*values: float) -> list[float]:
    return [*values, *([0.0] * (1536 - len(values)))]


def seed(session: Session) -> None:
    records = [
        ("knowledge/a.md", "doc A", vector(1.0, 0.0, 0.0), MODEL_A, True),
        ("knowledge/b.md", "doc B", vector(0.8, 0.6, 0.0), MODEL_A, True),
        ("knowledge/c.md", "doc C", vector(0.0, 1.0, 0.0), MODEL_A, True),
        ("knowledge/versioned.md", "inactive v1", vector(1.0, 0.0, 0.0), MODEL_A, False),
        ("knowledge/versioned.md", "active v2", vector(0.0, 0.0, 1.0), MODEL_A, True),
        ("knowledge/model-b.md", "other model", vector(1.0, 0.0, 0.0), MODEL_B, True),
    ]
    for ordinal, (source_uri, content, embedding, model_id, is_active) in enumerate(records):
        document = KnowledgeDocument(source_uri=source_uri, sha256=f"{ordinal:064d}", version=ordinal + 1, is_active=is_active, content=content)
        session.add(document)
        session.flush()
        chunk = KnowledgeChunk(document_id=document.id, ordinal=0, content=content)
        session.add(chunk)
        session.flush()
        session.add(KnowledgeEmbedding(chunk_id=chunk.id, embedding=embedding, embedding_model=model_id))
    session.commit()


def query_service(session: Session) -> KnowledgeService:
    return KnowledgeService(session, FixedEmbeddingProvider({"query": vector(1.0, 0.0, 0.0), "bad": [1.0]}))


def result_contents(session: Session, limit: int) -> list[tuple[str, float]]:
    return [(chunk.content, chunk.score or 0.0) for chunk in query_service(session).search("query", limit)]


@pytest.fixture
def postgres_session() -> Session:
    postgres_url = os.environ.get(INTEGRATION_URL_ENV)
    if not postgres_url:
        pytest.skip(f"set {INTEGRATION_URL_ENV} to run PostgreSQL retrieval integration tests")
    if not postgres_url.startswith("postgresql+") or "test" not in postgres_url.lower():
        pytest.fail(f"{INTEGRATION_URL_ENV} must target a dedicated PostgreSQL test database")
    environment = os.environ | {"APP_ENV": "test", "DATABASE_URL": postgres_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, env=environment, check=True)
    engine = create_engine(postgres_url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE knowledge_embeddings, knowledge_chunks, knowledge_documents CASCADE"))
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def sqlite_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_postgres_pgvector_retrieval_matches_sqlite_fallback(postgres_session: Session, sqlite_session: Session) -> None:
    seed(postgres_session)
    seed(sqlite_session)

    postgres_results = result_contents(postgres_session, 3)
    sqlite_results = result_contents(sqlite_session, 3)

    assert [content for content, _ in postgres_results] == ["doc A", "doc B", "doc C"]
    assert [content for content, _ in postgres_results] == [content for content, _ in sqlite_results]
    for (_, postgres_score), (_, sqlite_score) in zip(postgres_results, sqlite_results, strict=True):
        assert postgres_score == pytest.approx(sqlite_score, abs=1e-6)


def test_postgres_pgvector_filters_inactive_and_model_mismatch_and_limits(postgres_session: Session) -> None:
    seed(postgres_session)

    assert [content for content, _ in result_contents(postgres_session, 1)] == ["doc A"]
    assert [content for content, _ in result_contents(postgres_session, 2)] == ["doc A", "doc B"]
    contents = [content for content, _ in result_contents(postgres_session, 20)]
    assert contents == ["doc A", "doc B", "doc C", "active v2"]
    assert "inactive v1" not in contents
    assert "other model" not in contents


def test_postgres_pgvector_empty_result_and_dimension_error(postgres_session: Session) -> None:
    service = query_service(postgres_session)

    assert service.search("query") == []
    with pytest.raises(ValueError, match="1536 finite values"):
        service.search("bad")