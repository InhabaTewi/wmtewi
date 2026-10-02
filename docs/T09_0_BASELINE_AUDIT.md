# T09-0 Productization Baseline Audit

Date: 2026-10-02

## Scope and Baseline

This is a source-only audit. It does not connect to production, apply a migration, start a Worker, modify Nginx, or change Core behavior.

The T09 branch is `t09-productization`, created from the final-acceptance tag `t08-7-latency-hardening` at commit `a73b17d498a562e5097e6d9811d943ea2b912bb7` (`docs(t08): record production acceptance`). The stated production baseline remains active Core `1515` / `localhost/inaba-core:6526779`, rollback slot `1516` / `localhost/inaba-core:277fda1`, Nginx `/tewi -> 1515`, and PostgreSQL Alembic revision `20260925_0006`.

Untracked local `logs/` evidence was present before branch creation and is intentionally untouched and excluded from the T09 commit.

## Module Relationship

```text
Client / future Web / future QQ
        |
        v
Core API (apps/control_api/main.py)
        |
        +-- service-token middleware for every /api/* route
        +-- ChatService
        |     +-- ProviderRouter -> cloud / local Worker provider
        |     +-- KnowledgeService -> embeddings + pgvector search
        |     +-- ContextBuilder -> Persona + Memory + history + knowledge
        |     +-- SessionRecord, Message, InteractionTrace persistence
        |
        +-- PersonaRepository / MemoryService / KnowledgeService
        +-- WorkerRegistryService / InferenceJobService
        |
        v
PostgreSQL + pgvector, Alembic migrations

Windows gpu_worker -> Worker Registry / inference-job claim endpoints -> local llama.cpp
Deployment -> loopback Core, Nginx /tewi prefix, PostgreSQL container
```

### Core API

`apps/control_api/main.py` is one FastAPI application. `apps/control_api/dependencies.py` owns database sessions, readiness probes, service-token validation, provider-router construction, and knowledge-service construction. `/health`, `/health/live`, and `/health/ready` are anonymous; every path under `/api/` requires the one configured `SERVICE_TOKEN`.

### Provider Router

`packages/providers/` defines the provider protocol, cloud OpenAI-compatible provider, local Worker provider, typed failure policy, and `PreferLocalWithCloudFallbackProvider`. `ProviderRouter` selects cloud, local Worker, or prefer-local-with-cloud-fallback based on settings. Chat persistence records provider/failover/timing metadata in the trace payload.

### ContextBuilder, Memory, Knowledge, Persona

`packages/context_builder/builder.py` builds ordered context: active Persona, safety policy, confirmed contextual Memory, Knowledge chunks, optional API-mode behavior examples, recent session messages, then the current user input. `packages/memory/` persists candidates, confirmation, supersession, and persona/subject/session filtering. `packages/knowledge/` versions documents, chunks content, stores embeddings, and uses PostgreSQL vector search when available. `packages/persona/` versions YAML-backed personas and exposes active versions.

### Worker, Session/Message, Trace/Timing, Deployment

`apps/gpu_worker/` is an outbound Windows Worker: GPU probe, local-model probe, registry registration/heartbeat, and inference-job claim/complete/fail. It does not own business data. `packages/chat/service.py` creates sessions on first message and persists paired user/assistant messages and an `interaction_traces` payload with provider and timing metadata. Deployment scripts and `deploy/` keep Core loopback-bound behind Nginx; they are out of scope for this audit.

## Database Inventory

Models are in `packages/persistence/models.py`; migration history is `20260921_0001` through `20260925_0006`. No production database was queried or changed.

| Table | Exists | Primary key and purpose | Chat relationship | Web productization assessment |
| --- | --- | --- | --- | --- |
| `persona_versions` | Yes | UUID `id`; versioned persona prompts and metadata | Session may reference a persona version; chat resolves active persona | Reusable; admin lifecycle API is incomplete |
| `sessions` | Yes | string `id`; channel, external `user_id`, optional persona version | Parent of messages and session-scoped memory | Partial only: `user_id` is caller-controlled text, not a user FK; needs authenticated owner |
| `messages` | Yes | UUID `id`; role, content, session FK, trace ID | Persistent chat history | Reusable; access must be enforced through session owner; add author metadata only if needed |
| `memory_atoms` / `memory_links` | Yes | UUID atom ID / composite link key; memory lifecycle and relations | Context is filtered by persona, subject, and session | Reusable after mapping authenticated user ID to subject policy |
| `knowledge_documents` | Yes | UUID `id`; source, version, hash, active content | Retrieved before ContextBuilder | Reusable; needs user/admin authorization, not schema ownership by default |
| `knowledge_chunks` | Yes | UUID `id`; document FK and ordinal/content | Referenced in trace/context | Reusable |
| `knowledge_embeddings` | Yes | UUID `id`; one embedding per chunk | pgvector retrieval source | Reusable; no product UI is present |
| `behavior_examples` | Yes | UUID `id`; persona-version FK | Schema exists but normal ChatService does not supply examples | Defer UI until retrieval/use policy is defined |
| `interaction_traces` | Yes | UUID `id`; event ID and JSON payload | One trace per successful chat; failure traces for eligible failover paths | Reusable T07 data source; no browse/read API or user visibility policy |
| `feedback` | Yes | UUID `id`; trace ID, rating, correction | Intended trace feedback | Table exists; no endpoint or product flow |
| `training_candidates`, `dataset_snapshots`, `training_jobs`, `training_runs`, `model_versions` | Yes | UUID IDs; training/model metadata | Indirect trace lineage | Out of current T09 implementation scope |
| `worker_nodes` | Yes | UUID `id`, unique `worker_id` | Provider availability/inference capacity | Reusable for an admin read-only status screen |
| `inference_jobs` | Yes | UUID `id`; durable queued/claimed/completed inference work | Local provider job execution | Service/internal only; not a Web-user API |
| `users` | No | N/A | N/A | Required in T09-1 |
| `oauth_accounts` | No | N/A | N/A | Future provider identity mapping; do not add QQ OAuth in T09-1 |
| `auth_sessions` / user tokens | No | N/A | N/A | Required in T09-1 |

### Ownership Finding

Current `sessions.user_id` and `ChatRequest.user_id` allow a caller to supply an arbitrary string. `ChatService` trusts that string, creates a `SessionRecord` on first use, and ContextBuilder uses it to isolate memory. This is useful channel metadata but not authenticated ownership: there is no `users` table, no foreign key, no login middleware, no token/session mapping, and no session access check. `messages` inherit only the session foreign key. Therefore:

```text
EXISTING_AUTH=PARTIAL (service token only)
EXISTING_USER_OWNERSHIP=NO
```

## API Inventory and Exposure Assessment

| API group | Current routes | Current access | Web suitability | T09 disposition |
| --- | --- | --- | --- | --- |
| Health | `GET /health`, `/health/live`, `/health/ready` | Public | Operational only | Keep anonymous; do not use as app API |
| Chat | `POST /api/chat` | Service-token protected | Not directly safe: caller supplies user/session IDs | Add user-facing session/message API and authenticated chat facade; preserve current route for service/channel callers |
| Persona | `GET /api/personas/{persona_id}/active` | Service-token protected | Read path reusable | Separate user read from admin write/lifecycle later |
| Memory | search, candidate create, confirm, supersede, subject lookup under `/api/memory` | Service-token protected | Not directly safe: subject lookup can cross users | Keep internal/service API; add owner-filtered user views only after policy |
| Knowledge | document ingest, reindex, search, deactivate | Service-token protected | Search can be reused; mutation is admin-only | Split future user read/admin write boundaries |
| Workers | register, heartbeat, list, detail | Service-token protected | List/detail suitable for admin read-only view | Keep register/heartbeat service-only; add role-gated admin view |
| Inference jobs | claim, complete, fail | Service-token protected | No | Service/Worker internal only |
| Provider/readiness | readiness is health; selection is dependency-only | Public health or internal implementation | Admin diagnostics only | Do not expose provider credentials or failure internals |
| Trace/feedback | persistence only, no routes | N/A | Future admin/user feedback design | T09 may add product feedback entrypoint later; T07 remains out of scope now |

There are no user-token endpoints, OAuth endpoints, admin routes, debug routes, or browser-oriented session/message list routes.

## Migrations and Tests

Alembic head is `20260925_0006`:

1. `20260921_0001_initial`: core domain tables.
2. `20260921_0002_knowledge_vector`: document versions and pgvector conversion.
3. `20260921_0003_memory_isolation`: persona/session fields and indexes for memory.
4. `20260922_0004_knowledge_vector_search_index`: PostgreSQL HNSW cosine retrieval index.
5. `20260924_0005_worker_registry_metadata`: Worker Registry identity/status/GPU/model metadata.
6. `20260925_0006_inference_jobs`: durable inference-job queue.

The next user/auth migration must be forward-only from `20260925_0006`; it must not be applied to production during T09-0.

Existing tests cover schemas, chat/context persistence, memory and knowledge services, Persona API, service-token auth/readiness, cloud/local providers, Worker runtime/registry/inference jobs, PostgreSQL migrations/retrieval, CLI, and deployment assets. There are no tests for login, user ownership, browser flows, or a QQ adapter.

## Frontend and QQ Audit

```text
FRONTEND_EXISTING=NO
QQ_ASSETS_IN_MONOREPO=NO
```

There is no `apps/web`, `frontend`, `package.json`, package lockfile, React/Vue/Next/Vite configuration, Playwright/Vitest configuration, static application asset directory, or existing page implementation.

There is no NoneBot2, OneBot, NapCat, Lagrange, qqBot configuration, or Core-channel bridge implementation in this repository. The implementation specification and M01 documents describe an externally existing QQ estate and explicitly defer its modification. T09 should build the Web path first, then introduce QQ as a separate adapter which calls the same Core application boundary.

## Audit Conclusion

The current Core already has the correct reusable business primitives: Persona, Memory, Knowledge/RAG, conversation persistence, traces/timing, provider routing, Worker Registry, and durable local-inference jobs. The product boundary is missing: real users, user credentials, session ownership enforcement, browser APIs, UI, admin RBAC, and a channel adapter.

Recommended next task: **T09-1 User and Session Foundation**.