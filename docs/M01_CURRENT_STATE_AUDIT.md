# M01 Cloud Core Current-State Audit

Date: 2026-09-22

## Scope and Method

This is a read-only audit of the T01-T06 baseline. No M01 production capability, GPU Worker, training, QQ Adapter, ASR, TTS, Live2D, OBS, or game feature was implemented. No external qqBot, OneBot, NapCat, or Lagrange service or directory was inspected or modified.

The repository currently identifies its T01-T06 baseline at commit `755e107` (`feat: complete T01-T06 cloud-ready core`, tag `v0.1.0-core`). At audit time, `M01_CLOUD_CORE_TASK.md` is untracked; no tracked implementation file is modified.

## T01-T06 Mapping

| Task | Actual implementation |
| --- | --- |
| T01 - Monorepo and schemas | Python monorepo roots `apps/` and `packages/`; cross-module Pydantic DTOs in `packages/schemas/`; packaging and test configuration in `pyproject.toml`; schema coverage in `tests/test_schemas.py`. |
| T02 - PostgreSQL, pgvector, Alembic | SQLAlchemy models in `packages/persistence/models.py`; settings in `packages/persistence/config.py`; database dependency in `apps/control_api/dependencies.py`; Alembic environment and revisions under `migrations/`; local PostgreSQL container in `docker-compose.cloud.yml`. |
| T03 - Persona Service | YAML package `configs/persona/inaba.yaml`; import service and repository in `packages/persona/`; active-persona API in `apps/control_api/main.py`; unit/API tests in `tests/test_persona.py` and `tests/test_persona_api.py`. |
| T04 - Shared Memory Service | DTOs in `packages/schemas/memory.py`; repository and service in `packages/memory/`; memory APIs in `apps/control_api/main.py`; behavior and isolation coverage in `tests/test_memory.py` and `tests/test_memory_acceptance.py`. |
| T05 - External API Provider and Context Builder | Provider protocol, OpenAI-compatible provider, and router in `packages/providers/`; context construction in `packages/context_builder/builder.py`; chat persistence/orchestration in `packages/chat/service.py`; APIs in `apps/control_api/main.py`; tests in `tests/test_context_builder.py`, `tests/test_external_openai_provider.py`, `tests/test_provider_router.py`, `tests/test_chat_service.py`, and `tests/test_chat_context_acceptance.py`. |
| T06 - Knowledge/RAG Service | Embedding protocol/providers in `packages/knowledge/embedding.py`; repository/service in `packages/knowledge/`; knowledge APIs in `apps/control_api/main.py`; behavior coverage in `tests/test_knowledge.py` and chat-context acceptance coverage. |

## Core API and Configuration

The Core API is a single FastAPI application exposed as `apps.control_api.main:app`. The documented development command is `python -m uvicorn apps.control_api.main:app --reload`. It registers `/health`, Persona, Memory, Chat, and Knowledge endpoints. There is no application factory, startup migration/bootstrap hook, authentication middleware, CORS configuration, structured logging setup, or production-only entrypoint.

`packages/persistence/config.py` defines one `pydantic-settings` `Settings` object. It reads `.env` in the current working directory and ignores extra fields. Current fields are:

- `DATABASE_URL` through `database_url`, defaulting to local PostgreSQL;
- `EXTERNAL_LLM_BASE_URL`, `EXTERNAL_LLM_API_KEY`, and `EXTERNAL_LLM_MODEL`;
- `EMBEDDING_MODEL`.

The embedding endpoint and key are implicitly reused from the external LLM fields. There is no `APP_ENV`, separate LLM/embedding provider selection or credentials, `SERVICE_TOKEN`, log level, CORS/allowed-host configuration, knowledge-data path, backup-data path, startup validation, or committed `.env.example`.

## PostgreSQL, pgvector, and Migrations

`packages/persistence/models.py` declares the full planned core table set, including persona versions, sessions/messages, memory atoms/links, knowledge documents/chunks/embeddings, behavior examples, traces/feedback/training metadata, model versions, and worker nodes. `KnowledgeEmbedding.embedding` uses `Vector(1536)` for PostgreSQL and JSON for SQLite. The current Knowledge service does not issue a pgvector nearest-neighbor SQL query; it loads active chunk embeddings and calculates cosine similarity in Python.

Alembic uses `migrations/env.py`, which obtains the database URL from `Settings` and uses SQLAlchemy model metadata as its target metadata. Existing revisions are:

1. `20260921_0001_initial.py`: enables `vector` with `CREATE EXTENSION IF NOT EXISTS vector`, then calls `Base.metadata.create_all()`; downgrade calls `Base.metadata.drop_all()`.
2. `20260921_0002_knowledge_vector.py`: versions knowledge documents, adds `is_active`, and converts the embedding column to `vector(1536)` when applicable.
3. `20260921_0003_memory_isolation.py`: adds/indexes `persona_id` and `session_id` on `memory_atoms`.

The extension is initialized idempotently, and revisions 2-3 have conditional PostgreSQL DDL. The initial migration's `create_all()`/`drop_all()` is not a production-grade explicit migration history and conflicts with M01's requirement not to use `create_all()` as a migration substitute. Runtime engine configuration currently has only `pool_pre_ping=True`; no explicit pool size, recycle/connect timeout, or statement timeout is configured.

## Persona Service

Persona source is a YAML `PersonaPackage` containing `persona_id`, `version`, `display_name`, `system_prompt`, and metadata. `PersonaService.import_yaml()` parses a chosen file and delegates to `PersonaRepository.upsert_version()`. The repository stores the package in `persona_versions`, enforces unique `(persona_id, version)`, and, by default, deactivates all versions for that persona before activating the imported version.

The active version is read by the Context Builder and available at `GET /api/personas/{persona_id}/active`. The shipped configuration is `configs/persona/inaba.yaml`, version `inaba-1`. There is no import CLI, manifest directory, automatic loading at API startup, production config path, or API for persona import/activation beyond direct service use.

## Memory Service

Memory is persisted as `memory_atoms`, using the Pydantic `MemoryAtom`/candidate DTOs and `MemoryService`; it is not held in JSON files or provider-local state. Candidate creation deduplicates active records by exact `(content, scope, subject_id)`, creates unconfirmed atoms, and stores a `source_runtime_mode` of `local`, `api`, `import`, or `human`. Confirmation marks an atom confirmed. Superseding expires the prior atom through `valid_to`, creates a successor with incremented version, and records `supersedes_id`.

Retrieval excludes expired and, by default, unconfirmed atoms. It can filter by `scope`, `subject_id`, `persona_id`, and `session_id`; optional text retrieval is `ILIKE` substring matching, ordered by importance. The chat Context Builder calls `by_context(persona_id, user_id, session_id)`, so chat context is jointly isolated by persona, user, and session. This is a stricter implementation than a generic shared scope alone: the `scope` field remains stored and searchable, but it is not itself interpreted as an access-control policy.

Memory APIs currently expose search, candidate creation, confirmation, supersession, and subject lookup. There is no memory import CLI/format, no authenticated boundary, no database-side full-text/vector retrieval, and no policy engine for confidence thresholds or semantic conflict resolution. Tests demonstrate local/API-origin persistence, deduplication/supersession, SQLite restart persistence, and persona/user/session isolation.

## Knowledge/RAG

Knowledge documents are accepted as API request text with a `source_uri`; there is no filesystem source importer or background worker. Ingestion normalizes CRLF and excess blank lines, calculates SHA-256, preserves an active document when its normalized content is unchanged, otherwise deactivates all versions for the source and inserts a new version. Splitting starts new sections at Markdown heading lines and then slices each section into fixed 1,600-character substrings.

The service embeds all chunks through an `EmbeddingProvider`, stores each chunk and its embedding model ID, and stores raw normalized document content in PostgreSQL. Reindexing reads the active document and intentionally creates a new document version, even when its content is unchanged. Deletion only deactivates a document.

Search embeds the query, loads all active chunk embeddings, calculates cosine scores in Python, sorts descending, and returns the requested limit. The Chat Service searches for relevant chunks before context assembly, so current chat flow is:

`request -> select provider -> retrieve Knowledge -> load Persona/recent messages/confirmed contextual Memory -> assemble messages -> generate -> persist messages, memory candidates, trace`.

The order inside `ContextBuilder.to_messages()` is Persona, safety/tool policy, Memory, Knowledge, API-mode behavior examples, recent messages, and current user input. This aligns with the intended context ordering. Behavior examples are modeled but are not retrieved or passed by `ChatService`, so the API-mode few-shot section is currently empty in normal API use.

## LLM and Embedding Providers

`packages/providers/base.py` defines an `LLMProvider` protocol with `health()` and structured `generate()`. `ExternalOpenAIProvider` is the only concrete LLM provider. It calls an OpenAI-compatible `/chat/completions` endpoint over `httpx.AsyncClient`, requests JSON-schema output, propagates a trace header, uses a 60-second client timeout, and maps HTTP failures to `ProviderUnavailableError`. `ProviderRouter` prefers an optional healthy local provider and otherwise routes to the healthy external provider; no local implementation is present or wired into the API.

`packages/knowledge/embedding.py` separately defines the `EmbeddingProvider` protocol. Its production implementation is `ExternalOpenAIEmbeddingProvider`, which sends synchronous `httpx.post()` requests to `/embeddings` with a 30-second timeout and requires exactly 1,536 dimensions. `HashEmbeddingProvider` is deterministic and explicitly limited to tests/offline development.

There are no imports of CUDA, PyTorch, vLLM, sentence-transformers, or local GPU model code in the audited implementation. The production embedding implementation therefore does not require a local GPU; it requires a reachable OpenAI-compatible embedding API. It presently shares LLM base URL and API key, has no independently configured provider, no embedding health operation, and hard-codes 1,536 dimensions in both model schema and validation.

## Current Docker Compose

`docker-compose.cloud.yml` currently starts only `postgres` using `pgvector/pgvector:pg17`, a named `postgres_data` volume, and a `pg_isready` health check. It publishes `5432:5432` on all host interfaces and uses committed development credentials (`inaba`/`inaba`). It includes no Core API, reverse proxy, internal network, restart policy, API or database log rotation, migration job, secrets file, readiness wiring, knowledge worker, or persistent source/backup volume.

It is GPU-free and can run its database component on a non-GPU server. It is not an M01-ready no-GPU Cloud Core deployment because it cannot run the API and exposes PostgreSQL publicly by default.

## Automated Test Coverage

The suite contains 12 test modules. It uses in-memory SQLite for ordinary tests and a file-backed SQLite database for one restart-persistence acceptance test; it creates test schemas with `Base.metadata.create_all()`. Coverage includes schemas, Persona YAML import/version activation/API read, Memory lifecycle/isolation/persistence, context ordering, chat persistence/context assembly, provider protocol routing, OpenAI-compatible LLM request formatting, and Knowledge version/reindex/search behavior using `HashEmbeddingProvider`.

The audit attempted `pytest` from the workspace root, but the command is unavailable in the current shell (`pytest: command not found`); therefore no passing test count is claimed. `rg` is also absent. The test inventory above is from direct source review, not an execution result.

Missing automated coverage includes actual PostgreSQL/pgvector queries and migrations, container deployment, readiness/liveness, secret/auth handling, provider and embedding configuration validation, cloud embedding request behavior, backup/restore, persistent Docker restart, and the required M01 smoke flow.

## Gap Against M01 Cloud Core

The current code supplies the intended domain boundaries and a functional development baseline, but it does not yet satisfy M01 production requirements:

- Production configuration, `.env.example`, validation, separated LLM/embedding configuration, log/CORS/allowed-host settings, service token, and data paths are absent.
- Only `/health` exists. Required `/health/live` and `/health/ready` checks for DB, pgvector, Persona, and configured providers are absent.
- No service authentication exists; all current management/import-adjacent endpoints are open.
- The Core API is not containerized and no production Compose, reverse proxy, TLS template, internal network, restart policy, or log control exists.
- PostgreSQL is publicly published by the development Compose. Connection pool and timeout production controls are incomplete.
- Initial migration relies on metadata-wide create/drop rather than explicit schema operations.
- There are no CLI importers for Persona, Memory, Knowledge, or reindex, and no defined portable Memory import format.
- There are no backup/restore scripts or documentation, knowledge source/manifest persistence directory, deployment guide, or smoke test.
- Knowledge retrieval is functionally correct for small development datasets but scores every active embedding in Python rather than using pgvector nearest-neighbor indexing; embedding dimensions are fixed at 1,536.
- External LLM failures are standardized, but provider error detail/timeout policy and embedding-provider health/configuration are incomplete for production.
- Trace persistence exists but is below the V3 full lineage model and has no public trace/feedback flow; this remains out of M01 scope unless required for operation.

## M01 File Plan

The following is a planning inventory, not an implementation instruction executed by this audit.

### Modify

- `packages/persistence/config.py`: production settings, environment validation, separated LLM/embedding settings, paths, logging, CORS, and service authentication configuration.
- `apps/control_api/main.py`: liveness/readiness endpoints, authentication enforcement, production middleware/lifecycle wiring, and stable import-facing API contracts as needed.
- `apps/control_api/dependencies.py`: database pool/timeout configuration and reusable readiness/auth dependencies.
- `packages/providers/base.py`, `packages/providers/external_openai.py`, and `packages/providers/router.py`: standardized operational errors, configuration-aware health, and future-local-provider-compatible routing without implementing GPU work.
- `packages/knowledge/embedding.py`, `packages/knowledge/service.py`, and `packages/knowledge/repository.py`: cloud embedding configuration, model/version metadata/reindex behavior, operational health, and PostgreSQL-side vector retrieval where appropriate.
- `packages/persistence/models.py` and new Alembic revisions: only schema additions needed for M01 metadata/indexes; replace future migration reliance on metadata-wide creation with explicit DDL.
- `docker-compose.cloud.yml` or supersede it with a clearly documented production equivalent; keep it as development-only if retained.
- `README.md`: distinguish local development from production deployment and reference the new operational documents.
- Tests: add focused tests for M01 configuration, auth, health/readiness, cloud embeddings, PostgreSQL migration behavior, and smoke/backup contract.

### Add

- `.env.example` with non-secret production configuration template and field documentation.
- `deploy/Dockerfile.core` (or equivalent), `deploy/docker-compose.prod.yml`, and reverse-proxy configuration template.
- `scripts/backup.sh`, `scripts/restore.sh`, import/reindex CLI modules or scripts, and an M01 smoke-test script.
- `docs/M01_CLOUD_DEPLOYMENT.md` and `docs/BACKUP_RESTORE.md`.
- Knowledge-source/manifest handling and a production data-directory convention, if implemented outside PostgreSQL.
- New explicit Alembic migrations for all M01 schema changes.

### Preserve Without Rewrite

- `packages/schemas/`: retain the existing cross-module DTO boundary and extend it compatibly.
- `packages/persona/` and `configs/persona/inaba.yaml`: retain YAML-to-versioned-DB Persona import and active-version semantics.
- `packages/memory/`: retain `MemoryService` as the sole memory-write path, candidate/confirm/supersede lifecycle, and persona/user/session filtering.
- `packages/context_builder/builder.py`: retain the shared context builder and its ordered Persona/Policy/Memory/Knowledge/history/current-event assembly.
- `packages/chat/service.py`: retain the single chat orchestration path, trace ID generation, message persistence, and MemoryService writeback.
- `packages/providers/`: retain the `LLMProvider` protocol and router boundary; future cloud/local changes belong behind this abstraction.
- `packages/knowledge/`: retain document versioning, normalized-content hashing, model ID storage, and provider abstraction; evolve retrieval operationally rather than replace the service.
- `migrations/` and the existing migration chain: preserve migration history; add forward-only corrective revisions rather than rebuilding deployed state.
- Existing T01-T06 test modules: retain them as regression coverage while adding production/integration tests.

## M01 Boundary

M01 should productionize the existing Core without changing its domain ownership: PostgreSQL remains the authority for Persona, Memory, Knowledge, sessions, and conversation records; providers remain replaceable execution dependencies. GPU Worker, training, QQ integration, media/avatar, OBS, and game functionality remain explicitly deferred.

## M01-4 Retrieval Implementation Record

Before M01-4, the actual retrieval chain was `KnowledgeService.search` or `KnowledgeService.asearch` -> `EmbeddingProvider.embed` or `EmbeddingProvider.aembed` -> `KnowledgeRepository.active_chunk_embeddings` -> load every active embedding -> Python cosine calculation -> descending sort -> top-k slice. The implementation preserves ingestion, normalized-content hashing, versioning, reindexing, and deletion semantics.

M01-4 moves only the PostgreSQL branch to `KnowledgeRepository.search`: query embeddings are filtered by active document and `embedding_model`, then ordered by pgvector cosine distance (`embedding <=> query_vector`) with `LIMIT` in SQL. The returned public score remains similarity (`1 - cosine_distance`). SQLite retains the previous in-process cosine fallback with the same active-document and model filters.