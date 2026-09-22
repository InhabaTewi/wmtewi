"""Auditable import, export, and reindex commands for Cloud Core data."""

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from apps.control_api.dependencies import create_database_engine, create_knowledge_service
from packages.knowledge.service import KnowledgeService
from packages.memory.service import MemoryService
from packages.persona.service import PersonaService
from packages.persistence.config import settings
from packages.schemas.knowledge import KnowledgeDocumentCreate
from packages.schemas.memory import MemoryImportAtom
from packages.schemas.persona import PersonaPackage

SUPPORTED_KNOWLEDGE_SUFFIXES = {".md", ".txt"}


@dataclass
class Summary:
    processed: int = 0
    created: int = 0
    skipped: int = 0
    conflicted: int = 0
    failed: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)
    timestamp: str | None = None
    operation: str | None = None
    source: str | None = None


def _emit(summary: Summary, *, as_json: bool) -> None:
    data = asdict(summary)
    if as_json:
        print(json.dumps(data, sort_keys=True))
    else:
        print(" ".join(f"{name}={value}" for name, value in data.items()))


def _read_jsonl(path: Path) -> list[MemoryImportAtom]:
    records: list[MemoryImportAtom] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(MemoryImportAtom.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"invalid JSONL record at line {line_number}") from exc
    return records


def memory_export(
    session: Session,
    output: Path,
    *,
    persona_id: str | None,
    subject_id: str | None,
    include_history: bool,
    active_only: bool = False,
    confirmed_only: bool = False,
) -> Summary:
    records = MemoryService(session).export_records(
        persona_id=persona_id,
        subject_id=subject_id,
        active_only=active_only or not include_history,
        confirmed_only=confirmed_only or not include_history,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8")
    return Summary(processed=len(records), created=len(records))


def memory_import(
    session: Session,
    records: list[MemoryImportAtom],
    *,
    dry_run: bool,
    on_conflict: str,
    continue_on_error: bool,
) -> Summary:
    service = MemoryService(session)
    summary = Summary(processed=len(records))
    pending = list(records)
    known_ids = {record.id for record in records}
    while pending:
        progressed = False
        for record in pending[:]:
            try:
                if record.supersedes_id is not None and record.supersedes_id in known_ids:
                    parent = service.repository.get(record.supersedes_id)
                    if parent is None and any(item.id == record.supersedes_id for item in pending):
                        continue
                existing = service.repository.get(record.id)
                if existing is not None:
                    if existing.content == record.content:
                        summary.skipped += 1
                    elif on_conflict == "skip":
                        summary.conflicted += 1
                    else:
                        raise ValueError(f"memory conflict for stable ID {record.id}")
                elif dry_run:
                    summary.created += 1
                else:
                    result = service.import_record(record, on_conflict=on_conflict)
                    field = "created" if result == "inserted" else result
                    setattr(summary, field, getattr(summary, field) + 1)
                pending.remove(record)
                progressed = True
            except ValueError as exc:
                summary.failed += 1
                summary.errors.append(str(exc))
                pending.remove(record)
                if not continue_on_error:
                    raise
        if not progressed:
            raise ValueError("unresolved supersedes_id reference in import")
    return summary


def _persona_package(path: Path) -> PersonaPackage:
    try:
        return PersonaPackage.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"invalid Persona YAML: {path}") from exc


def persona_import(session: Session, path: Path, *, activate: bool, dry_run: bool) -> Summary:
    package = _persona_package(path)
    service = PersonaService(session)
    existing = service.repository.get_version(package.persona_id, package.version)
    if existing is not None:
        same = (
            existing.display_name == package.display_name
            and existing.system_prompt == package.system_prompt
            and existing.metadata_ == package.metadata
        )
        if not same:
            raise ValueError(f"persona version conflict: {package.persona_id}/{package.version}")
        if activate and not existing.is_active:
            if not dry_run:
                service.import_package(package, activate=True)
            return Summary(processed=1, created=1)
        return Summary(processed=1, skipped=1)
    if not dry_run:
        service.import_package(package, activate=activate)
    return Summary(processed=1, created=1)


def knowledge_files(path: Path) -> tuple[Path, list[Path]]:
    root = path.resolve()
    if root.is_file():
        if root.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
            raise ValueError("knowledge import supports only .md and .txt files")
        return root.parent, [root]
    if not root.is_dir():
        raise ValueError(f"knowledge path does not exist: {path}")
    return root, sorted(item for item in root.rglob("*") if item.is_file())


def source_uri(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("knowledge file must be inside its source root") from exc
    return f"knowledge://{relative.as_posix()}"


async def knowledge_import(
    service: KnowledgeService,
    path: Path,
    *,
    dry_run: bool,
    max_file_bytes: int,
) -> Summary:
    root, files = knowledge_files(path)
    summary = Summary()
    for file_path in files:
        summary.processed += 1
        if file_path.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
            summary.ignored += 1
            continue
        if file_path.stat().st_size > max_file_bytes:
            raise ValueError(f"knowledge file exceeds {max_file_bytes} byte limit: {file_path}")
        content = file_path.read_text(encoding="utf-8")
        uri = source_uri(root, file_path)
        normalized = service._normalize(content)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        current = service.repository.active_by_source(uri)
        if current is not None and current.sha256 == digest:
            summary.skipped += 1
            continue
        if not dry_run:
            await service.aingest(KnowledgeDocumentCreate(source_uri=uri, content=content))
        summary.created += 1
    return summary


async def knowledge_reindex(service: KnowledgeService, *, source_uri_value: str | None, dry_run: bool) -> Summary:
    documents = service.repository.list_active(source_uri_value)
    if source_uri_value is not None and not documents:
        raise ValueError(f"active knowledge source not found: {source_uri_value}")
    summary = Summary(processed=len(documents))
    for document in documents:
        if not dry_run:
            await service.areindex(document.id)
        summary.created += 1
    return summary


def knowledge_list(session: Session) -> list[dict[str, str | int]]:
    return [
        {"id": str(document.id), "source_uri": document.source_uri, "version": document.version}
        for document in KnowledgeService(session, _NoEmbeddingProvider()).repository.list_active()
    ]


class _NoEmbeddingProvider:
    """List never embeds; this prevents an unnecessary external provider client."""

    model_id = "not-used"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a machine-readable summary")
    subparsers = parser.add_subparsers(dest="resource", required=True)

    persona = subparsers.add_parser("persona").add_subparsers(dest="action", required=True)
    persona_import_parser = persona.add_parser("import")
    persona_import_parser.add_argument("--file", type=Path, required=True)
    persona_import_parser.add_argument("--activate", action="store_true")
    persona_import_parser.add_argument("--dry-run", action="store_true")

    memory = subparsers.add_parser("memory").add_subparsers(dest="action", required=True)
    memory_export_parser = memory.add_parser("export")
    memory_export_parser.add_argument("--output", type=Path, required=True)
    memory_export_parser.add_argument("--persona-id")
    memory_export_parser.add_argument("--subject-id")
    memory_export_parser.add_argument("--include-history", action="store_true")
    memory_export_parser.add_argument("--active-only", action="store_true")
    memory_export_parser.add_argument("--confirmed-only", action="store_true")
    memory_import_parser = memory.add_parser("import")
    memory_import_parser.add_argument("--file", type=Path, required=True)
    memory_import_parser.add_argument("--dry-run", action="store_true")
    memory_import_parser.add_argument("--on-conflict", choices=("error", "skip"), default="error")
    memory_import_parser.add_argument("--continue-on-error", action="store_true")

    knowledge = subparsers.add_parser("knowledge").add_subparsers(dest="action", required=True)
    knowledge_import_parser = knowledge.add_parser("import")
    knowledge_import_parser.add_argument("--path", type=Path, required=True)
    knowledge_import_parser.add_argument("--dry-run", action="store_true")
    knowledge_reindex_parser = knowledge.add_parser("reindex")
    knowledge_reindex_parser.add_argument("--source-uri")
    knowledge_reindex_parser.add_argument("--dry-run", action="store_true")
    knowledge.add_parser("list")
    return parser


def _session() -> tuple[Session, object]:
    engine = create_database_engine(settings.database_url)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)(), engine


def run(arguments: argparse.Namespace) -> Summary:
    session, engine = _session()
    try:
        if arguments.resource == "persona":
            summary = persona_import(session, arguments.file, activate=arguments.activate, dry_run=arguments.dry_run)
        elif arguments.resource == "memory" and arguments.action == "export":
            summary = memory_export(
                session,
                arguments.output,
                persona_id=arguments.persona_id,
                subject_id=arguments.subject_id,
                include_history=arguments.include_history,
                active_only=arguments.active_only,
                confirmed_only=arguments.confirmed_only,
            )
        elif arguments.resource == "memory":
            summary = memory_import(
                session,
                _read_jsonl(arguments.file),
                dry_run=arguments.dry_run,
                on_conflict=arguments.on_conflict,
                continue_on_error=arguments.continue_on_error,
            )
        else:
            knowledge = create_knowledge_service(session)
            try:
                if arguments.action == "import":
                    summary = asyncio.run(
                        knowledge_import(
                            knowledge,
                            arguments.path,
                            dry_run=arguments.dry_run,
                            max_file_bytes=settings.knowledge_max_file_bytes,
                        )
                    )
                elif arguments.action == "reindex":
                    summary = asyncio.run(
                        knowledge_reindex(knowledge, source_uri_value=arguments.source_uri, dry_run=arguments.dry_run)
                    )
                else:
                    documents = knowledge.repository.list_active()
                    summary = Summary(processed=len(documents), created=len(documents))
            finally:
                asyncio.run(knowledge.embedding_provider.aclose())
        if not getattr(arguments, "dry_run", False):
            session.commit()
        summary.timestamp = datetime.now(UTC).isoformat()
        summary.operation = f"{arguments.resource}.{arguments.action}"
        source = getattr(arguments, "file", None) or getattr(arguments, "path", None) or getattr(arguments, "output", None)
        summary.source = str(source) if source is not None else getattr(arguments, "source_uri", None)
        return summary
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.resource == "knowledge" and arguments.action == "list":
            session, engine = _session()
            try:
                items = knowledge_list(session)
            finally:
                session.close()
                engine.dispose()
            if arguments.as_json:
                print(json.dumps(items, sort_keys=True))
            else:
                for item in items:
                    print(f"{item['source_uri']} version={item['version']} id={item['id']}")
            return 0
        summary = run(arguments)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _emit(summary, as_json=arguments.as_json)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
