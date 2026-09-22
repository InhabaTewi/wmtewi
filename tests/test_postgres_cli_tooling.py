import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from apps.control_api.cli import knowledge_import, memory_export, memory_import
from packages.knowledge.embedding import HashEmbeddingProvider
from packages.knowledge.service import KnowledgeService
from packages.memory.service import MemoryService
from packages.persistence.models import MemoryAtomRecord
from packages.schemas.memory import MemoryCandidateCreate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_URL_ENV = "POSTGRES_INTEGRATION_URL"


@pytest.fixture
def postgres_session() -> Session:
    postgres_url = os.environ.get(INTEGRATION_URL_ENV)
    if not postgres_url:
        pytest.skip(f"set {INTEGRATION_URL_ENV} to run PostgreSQL CLI tooling integration tests")
    environment = os.environ | {"APP_ENV": "test", "DATABASE_URL": postgres_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=PROJECT_ROOT, env=environment, check=True)
    engine = create_engine(postgres_url)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_postgres_memory_export_import_round_trip(postgres_session: Session, tmp_path: Path) -> None:
    memory = MemoryService(postgres_session).create_candidate(
        MemoryCandidateCreate(
            type="preference",
            content="PostgreSQL round trip memory.",
            scope="migration-test",
            persona_id="migration-persona",
            subject_id="migration-user",
            session_id="migration-session",
            source_runtime_mode="import",
        )
    )
    MemoryService(postgres_session).confirm(memory.id)
    postgres_session.commit()
    output = tmp_path / "memory.jsonl"
    memory_export(
        postgres_session,
        output,
        persona_id="migration-persona",
        subject_id="migration-user",
        include_history=True,
    )
    records = __import__("apps.control_api.cli", fromlist=["_read_jsonl"])._read_jsonl(output)
    postgres_session.delete(postgres_session.get(MemoryAtomRecord, memory.id))
    postgres_session.commit()

    summary = memory_import(postgres_session, records, dry_run=False, on_conflict="error", continue_on_error=False)
    postgres_session.commit()

    restored = postgres_session.get(MemoryAtomRecord, memory.id)
    assert summary.created == 1
    assert restored is not None
    assert restored.confirmed is True
    assert restored.session_id == "migration-session"


@pytest.mark.asyncio
async def test_postgres_knowledge_import_and_reindex(postgres_session: Session, tmp_path: Path) -> None:
    source_root = tmp_path / "knowledge"
    source_root.mkdir()
    filename = f"migration-{uuid4()}.md"
    (source_root / filename).write_text("# Migration\nPostgreSQL knowledge source.", encoding="utf-8")
    service = KnowledgeService(postgres_session, HashEmbeddingProvider())

    summary = await knowledge_import(service, source_root, dry_run=False, max_file_bytes=1024)
    postgres_session.commit()
    document = service.repository.active_by_source(f"knowledge://{filename}")
    assert summary.created == 1
    assert document is not None

    reindexed = await service.areindex(document.id)
    postgres_session.commit()
    assert reindexed.version == document.version + 1
