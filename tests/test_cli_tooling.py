import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.control_api import cli
from apps.control_api.cli import (
    _read_jsonl,
    knowledge_import,
    knowledge_list,
    knowledge_reindex,
    memory_export,
    memory_import,
    persona_import,
    source_uri,
)
from packages.knowledge.embedding import HashEmbeddingProvider
from packages.knowledge.service import KnowledgeService
from packages.memory.service import MemoryService
from packages.persistence.models import Base, MemoryAtomRecord
from packages.schemas.memory import MemoryCandidateCreate


def new_session() -> tuple[Session, object]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def test_persona_cli_import_is_idempotent_and_dry_run(session, tmp_path: Path) -> None:
    persona_file = tmp_path / "inaba.yaml"
    persona_file.write_text(
        "persona_id: inaba\nversion: cli-1\ndisplay_name: Inaba\nsystem_prompt: Stay alert.\n", encoding="utf-8"
    )

    assert persona_import(session, persona_file, activate=False, dry_run=True).created == 1
    assert persona_import(session, persona_file, activate=False, dry_run=False).created == 1
    assert persona_import(session, persona_file, activate=False, dry_run=False).skipped == 1
    assert persona_import(session, persona_file, activate=True, dry_run=False).created == 1

    persona_file.write_text(
        "persona_id: inaba\nversion: cli-1\ndisplay_name: Changed\nsystem_prompt: Stay alert.\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="persona version conflict"):
        persona_import(session, persona_file, activate=False, dry_run=False)


def test_persona_cli_rejects_malformed_yaml(session, tmp_path: Path) -> None:
    persona_file = tmp_path / "invalid.yaml"
    persona_file.write_text("persona_id: [", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid Persona YAML"):
        persona_import(session, persona_file, activate=False, dry_run=True)


def test_cli_json_summary_includes_audit_fields(monkeypatch, capsys) -> None:
    summary = cli.Summary(processed=1, created=1, timestamp="2026-09-22T00:00:00+00:00", operation="persona.import", source="inaba.yaml")
    monkeypatch.setattr(cli, "run", lambda arguments: summary)

    assert cli.main(["--json", "persona", "import", "--file", "inaba.yaml"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "persona.import"
    assert payload["source"] == "inaba.yaml"
    assert "service_token" not in payload


def test_memory_jsonl_round_trip_preserves_stable_ids_and_supersession(session, tmp_path: Path) -> None:
    source = MemoryService(session)
    original = source.create_candidate(
        MemoryCandidateCreate(
            type="preference",
            content="Prefers tea.",
            scope="shared",
            persona_id="inaba",
            subject_id="user-1",
            session_id="session-1",
            source_runtime_mode="import",
        )
    )
    source.confirm(original.id)
    replacement = source.supersede(
        original.id,
        MemoryCandidateCreate(
            type="preference",
            content="Prefers coffee.",
            scope="shared",
            persona_id="inaba",
            subject_id="user-1",
            session_id="session-1",
            source_runtime_mode="import",
        ),
    )
    source.confirm(replacement.id)
    session.commit()
    exported = tmp_path / "memory.jsonl"

    export_summary = memory_export(session, exported, persona_id="inaba", subject_id="user-1", include_history=True)
    assert export_summary.created == 2
    records = _read_jsonl(exported)
    assert {record.id for record in records} == {original.id, replacement.id}
    assert next(record for record in records if record.id == replacement.id).supersedes_id == original.id

    target, engine = new_session()
    try:
        imported = memory_import(target, records, dry_run=False, on_conflict="error", continue_on_error=False)
        target.commit()
        assert imported.created == 2
        restored = {record.id: record for record in target.query(MemoryAtomRecord).all()}
        assert restored[replacement.id].supersedes_id == original.id
        assert restored[replacement.id].confirmed is True
        assert restored[replacement.id].persona_id == "inaba"
        assert restored[replacement.id].session_id == "session-1"
        assert memory_import(target, records, dry_run=False, on_conflict="error", continue_on_error=False).skipped == 2
    finally:
        target.close()
        engine.dispose()


def test_memory_import_rejects_conflicts_and_malformed_jsonl(session, tmp_path: Path) -> None:
    source = MemoryService(session)
    memory = source.create_candidate(
        MemoryCandidateCreate(
            type="preference", content="Original", scope="shared", source_runtime_mode="import"
        )
    )
    source.confirm(memory.id)
    session.commit()
    exported = tmp_path / "memory.jsonl"
    memory_export(session, exported, persona_id=None, subject_id=None, include_history=True)
    data = json.loads(exported.read_text(encoding="utf-8").strip())
    data["content"] = "Changed"
    conflicting = _read_jsonl_path(tmp_path / "conflict.jsonl", data)

    with pytest.raises(ValueError, match="memory conflict"):
        memory_import(session, conflicting, dry_run=False, on_conflict="error", continue_on_error=False)
    assert memory_import(session, conflicting, dry_run=True, on_conflict="skip", continue_on_error=False).conflicted == 1

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSONL record"):
        _read_jsonl(invalid)


def _read_jsonl_path(path: Path, data: dict) -> list:
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return _read_jsonl(path)


@pytest.mark.asyncio
async def test_knowledge_cli_import_directory_versioning_dry_run_and_reindex(session, tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source_file = source_root / "world.md"
    source_file.write_text("# World\nFirst version.", encoding="utf-8")
    (source_root / "ignored.pdf").write_text("not supported", encoding="utf-8")
    service = KnowledgeService(session, HashEmbeddingProvider())

    first = await knowledge_import(service, source_root, dry_run=False, max_file_bytes=1024)
    assert (first.created, first.ignored) == (1, 1)
    session.commit()
    assert service.repository.list_active()[0].source_uri == "knowledge://world.md"

    assert (await knowledge_import(service, source_root, dry_run=False, max_file_bytes=1024)).skipped == 1
    source_file.write_text("# World\nSecond version.", encoding="utf-8")
    assert (await knowledge_import(service, source_root, dry_run=True, max_file_bytes=1024)).created == 1
    assert service.repository.list_active()[0].version == 1
    assert (await knowledge_import(service, source_root, dry_run=False, max_file_bytes=1024)).created == 1
    session.commit()
    active = service.repository.list_active()
    assert len(active) == 1 and active[0].version == 2
    assert (await knowledge_reindex(service, source_uri_value=active[0].source_uri, dry_run=False)).created == 1
    assert service.repository.list_active()[0].version == 3
    assert knowledge_list(session) == [
        {
            "id": str(service.repository.list_active()[0].id),
            "source_uri": "knowledge://world.md",
            "version": 3,
        }
    ]


@pytest.mark.asyncio
async def test_knowledge_cli_rejects_large_files_and_source_path_escape(session, tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    file_path = root / "large.txt"
    file_path.write_text("too large", encoding="utf-8")
    service = KnowledgeService(session, HashEmbeddingProvider())

    with pytest.raises(ValueError, match="exceeds 3 byte limit"):
        await knowledge_import(service, root, dry_run=True, max_file_bytes=3)
    with pytest.raises(ValueError, match="inside its source root"):
        source_uri(root, tmp_path / "outside.md")
