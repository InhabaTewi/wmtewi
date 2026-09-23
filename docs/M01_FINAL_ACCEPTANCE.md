# M01 Final Production Acceptance

Date: 2026-09-23

## Production Baseline

- Public endpoint: `http://wmtewi.space/tewi`
- Internal Core endpoint: `http://127.0.0.1:1515`
- Architecture: public Nginx `/tewi` prefix stripping -> loopback Core -> internal PostgreSQL + pgvector -> Qwen OpenAI-compatible LLM and embedding APIs.
- Git commit: `90d86ee` (`fix(m01): return not found for unknown paths`)
- Running Core image: `localhost/inaba-core:90d86ee`
- Running Core image ID: `a0dbd0b0fc54eec1befb6249e194c4828b642ac99a033cb0ecb946803efaf4fa`
- Alembic revision: `20260922_0004`
- PostgreSQL: `17.11`
- pgvector: `0.8.6`
- Active Persona: `inaba` / `inaba-1`
- LLM: `openai-compatible` / `qwen3.7-plus-2026-05-26`
- Embeddings: `openai-compatible` / `qwen3.7-text-embedding` / `1536` dimensions

## E2E Evidence

The acceptance run used the unique identifier `M01E2E_20260923T140422Z_cc608870`. No secret values or private conversation text are recorded here.

- Public protected endpoint without a Bearer token returned `401`; with the configured token it returned `200`.
- Public Persona chat returned a structured `ChatResult` with non-empty speech, `runtime_mode=api`, provider `external-openai`, and Qwen lineage in its persisted trace.
- A unique Knowledge document was ingested through the public API. Production PostgreSQL confirmed one active document, one chunk, one `qwen3.7-text-embedding` embedding, and `vector_dims=1536`.
- Public RAG chat returned the unique ingested fact. Its trace referenced the exact stored Knowledge chunk ID.
- Normal public Chat writeback produced Memory candidates. The acceptance candidate was confirmed through the protected Memory confirm API and was bound to `persona=inaba`, the acceptance user/session, and runtime mode `api`.
- Public Memory recall returned the unique test value. Its trace referenced the exact confirmed Memory atom ID.
- A combined public chat returned both unique facts. Its trace contained both the exact Memory atom ID and Knowledge chunk ID.
- Session, messages, and interaction traces were persisted in PostgreSQL.
- Core-only restart preserved public/local health and subsequent Memory recall plus Knowledge RAG.
- PostgreSQL-only restart preserved `pg_isready`, Core readiness, subsequent Memory recall, and Knowledge RAG. Core was not restarted for this test.
- Final logical backup `20260923-150004` completed and `verify_backup.sh` validated all checksums.

## Acceptance Matrix

| Capability | Result |
| --- | --- |
| Public ingress | PASS |
| Bearer authentication | PASS |
| Persona | PASS |
| Real Qwen LLM | PASS |
| Real Qwen Embedding 1536 | PASS |
| Knowledge ingestion | PASS |
| pgvector retrieval | PASS |
| RAG chat | PASS |
| Memory writeback | PASS |
| Memory retrieval | PASS |
| Memory chat recall | PASS |
| Persona + Memory + Knowledge context | PASS |
| Conversation persistence | PASS |
| Core restart persistence | PASS |
| PostgreSQL restart persistence | PASS |
| Backup | PASS |
| Provider failure safety | PASS (mock readiness tests assert `503`; production credentials were not changed) |
| Public prefix | PASS (`/tewi` is `301`, public health is `200`, direct prefixed Core route is `404`) |
| Existing wmtewi service | PASS (`/`, `/aigc/`, `/aigc/admin/` returned `200`) |
| SearXNG | PASS (running; not restarted) |
| PostgreSQL network isolation | PASS (no host `:5432` listener) |
| Secret leakage | PASS (programmatic checks of Core, Nginx, PostgreSQL logs and tracked files) |
| Full pytest | PASS (`88 passed`, `0 skipped`) |

## Production Fixes Found During Acceptance

- `a598f74`: flush a newly created session before writing its messages, fixing PostgreSQL foreign-key ordering on the first Chat request.
- `e0c5f7b`: bind Chat-generated Memory candidates to the originating user ID.
- `90d86ee`: authenticate only `/api/` routes so unknown direct Core paths return `404` instead of `401`.

Each fix was covered by targeted tests, a full integration-enabled regression run, an offline derived Core image build, and the affected public E2E checks.

## Cleanup and Known Limitation

The unique acceptance Knowledge document was deactivated through the protected Knowledge API. The project has no Memory delete/deactivate endpoint and no conversation cleanup API. After recording exact pre-delete counts, the uniquely tagged acceptance Memory atoms, messages, traces, and session were removed in one PostgreSQL transaction scoped to the acceptance session ID. This API gap is backlog work and did not block M01 acceptance.

M01 CLOUD CORE: PASS

Production Endpoint: `http://wmtewi.space/tewi`

Cloud Core is ready for T08 Local GPU Worker integration.