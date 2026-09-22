# M01 Data Migration Tooling

Cloud Core exposes a scriptable, auditable CLI at `python -m apps.control_api.cli`. It uses the configured Settings, database, and existing Persona, Memory, and Knowledge services directly; it does not call the HTTP API and does not require a Bearer token.

All mutation commands emit `processed`, `created`, `skipped`, `conflicted`, `failed`, `ignored`, and `errors` counters. Add `--json` before the resource name for machine-readable output. Exit code `0` means success; validation failures and continued per-record import failures return nonzero.

## Persona

Upload the Persona YAML source, not a database row:

```bash
python -m apps.control_api.cli persona import \
  --file configs/persona/inaba.yaml --activate
python -m apps.control_api.cli --json persona import \
  --file configs/persona/inaba.yaml --dry-run
```

The CLI preserves the existing `(persona_id, version)` model. An identical version is skipped, a different payload for the same version is rejected, and `--activate` explicitly selects the imported version. The pre-existing `PersonaService.import_yaml()` default activation behavior is unchanged; the CLI is deliberately explicit about activation.

## Memory

The default export is active, confirmed memory only. `--include-history` exports unconfirmed, expired, and superseded records too. JSONL preserves stable IDs, isolation fields, confirmation, version, supersession, timestamps, and source metadata.

```bash
python -m apps.control_api.cli memory export \
  --persona-id inaba --active-only --output memory.jsonl
python -m apps.control_api.cli memory export \
  --persona-id inaba --include-history --output memory-history.jsonl
python -m apps.control_api.cli memory import --file memory-history.jsonl
python -m apps.control_api.cli --json memory import \
  --file memory-history.jsonl --dry-run --on-conflict error
```

`--active-only` is the default behavior and is represented by omitting `--include-history`. A duplicate stable ID with the same content is skipped. A duplicate ID with different content fails by default; use `--on-conflict skip` to retain the existing record and count the conflict. JSONL is validated before it is written, including atom type, scope, persona ID, timestamps, score bounds, and supersession references. Imports fail fast by default; `--continue-on-error` reports record errors and exits nonzero.

## Knowledge

Knowledge import accepts UTF-8 `.md` and `.txt` files. A file uses `knowledge://<filename>`; a directory recursively uses stable paths relative to its source root, such as `knowledge://world/worldview.md`. Other files are ignored and counted. The default maximum input is 5 MiB per file and is configurable through `KNOWLEDGE_MAX_FILE_BYTES`.

```bash
python -m apps.control_api.cli knowledge import --path data/knowledge/sources
python -m apps.control_api.cli --json knowledge import \
  --path data/knowledge/sources --dry-run
python -m apps.control_api.cli knowledge list
python -m apps.control_api.cli knowledge reindex
python -m apps.control_api.cli knowledge reindex \
  --source-uri knowledge://world/worldview.md
```

Knowledge source paths are resolved with `pathlib`; source identities are never server absolute paths. The first version does not require a manifest: directory scanning is sufficient. A future optional manifest must resolve only paths beneath the source root; it must never be used to read arbitrary host paths.

The existing Knowledge Service controls normalized-content hashing, source identity, document versioning, deactivation, chunking, and embedding writes. Unchanged source content is skipped. Changed content creates a new active version and deactivates the old version. Reindex intentionally creates a new document version using the current embedding provider/model/dimension; this is existing KnowledgeService behavior and is not changed by this tooling.

## Local-to-Cloud Procedure

1. Upload Persona YAML sources and run `persona import`.
2. Export the local Memory database with `memory export`, copy only the JSONL file, then run `memory import` on Cloud Core.
3. Upload raw Markdown/text Knowledge sources under the production convention `/opt/inaba-data/knowledge/sources/` (development/test may use any directory), then run `knowledge import`.
4. Run `knowledge reindex` after changing the configured embedding model.

Do not copy PostgreSQL volumes, raw pgvector rows, API keys, `SERVICE_TOKEN`, `DATABASE_URL`, sessions/messages, traces, feedback, or training data. Secrets remain environment configuration only. Knowledge is always re-embedded by the Cloud Core provider.
