import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from packages.persistence.config import DEFAULT_EMBEDDING_DIMENSION
from packages.persistence.models import Base


INTEGRATION_URL_ENV = "POSTGRES_INTEGRATION_URL"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "persona_versions",
    "sessions",
    "messages",
    "memory_atoms",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_embeddings",
    "interaction_traces",
    "worker_nodes",
}


@pytest.fixture
def postgres_url() -> str:
    url = os.environ.get(INTEGRATION_URL_ENV)
    if not url:
        pytest.skip(f"set {INTEGRATION_URL_ENV} to run PostgreSQL migration integration tests")
    if not url.startswith("postgresql+") or "test" not in url.lower():
        pytest.fail(f"{INTEGRATION_URL_ENV} must target a dedicated PostgreSQL test database")
    return url


def run_alembic(revision: str, postgres_url: str) -> None:
    environment = os.environ | {"APP_ENV": "test", "DATABASE_URL": postgres_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade" if revision != "base" else "downgrade", revision],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )


def assert_postgres_schema(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260921_0003"
            assert EXPECTED_TABLES <= set(inspect(connection).get_table_names())
            embedding_type = connection.scalar(
                text(
                    "SELECT format_type(a.atttypid, a.atttypmod) "
                    "FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    "WHERE c.relname = 'knowledge_embeddings' AND a.attname = 'embedding'"
                )
            )
            assert embedding_type == f"vector({DEFAULT_EMBEDDING_DIMENSION})"
            metadata_tables = {table.name for table in Base.metadata.sorted_tables}
            assert metadata_tables <= set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def test_postgres_alembic_schema_is_repeatable(postgres_url: str) -> None:
    run_alembic("base", postgres_url)
    run_alembic("head", postgres_url)
    assert_postgres_schema(postgres_url)

    run_alembic("base", postgres_url)
    run_alembic("head", postgres_url)
    assert_postgres_schema(postgres_url)