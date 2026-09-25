# T08-4 Local E2E Acceptance

Date: 2026-09-26

## Scope And Boundaries

This acceptance ran only on Windows local infrastructure. It did not deploy Cloud Core, run a production migration, change production configuration, change Nginx, or contact an external LLM provider.

- Provider mode: `local_worker`
- Core listener: `127.0.0.1:18080`
- Worker: `home-5090-01`
- GPU: `NVIDIA GeForce RTX 5090`
- Local runtime: native Windows CUDA llama.cpp on `127.0.0.1:18081`
- Model: `Qwen/Qwen3.5-9B`, self-converted F16 GGUF
- Model revision: `c202236235762e1c871ad0ccb60c8ee5ba337b9a`
- E2E database: isolated local PostgreSQL database, not the pytest integration database
- PostgreSQL extension: pgvector `0.8.6`

The local Core used a process-only service token and dummy external LLM configuration. A local acceptance harness supplied the existing deterministic `HashEmbeddingProvider` for Knowledge retrieval. That embedding harness is separate from LLM routing; no external LLM was constructed or called.

## PostgreSQL And Migration

- Docker Desktop was restored normally and the existing loopback pgvector test container was reused.
- `pg_isready` succeeded.
- `0005 -> 0006`, `0006 -> 0005`, and `0005 -> 0006` were exercised by `tests/test_postgres_migrations.py`.
- A fresh isolated `inaba_t08_e2e` database upgraded through `0001` to `20260925_0006`.
- The PostgreSQL migration, pgvector, inference lifecycle, API, and concurrent-claim test set passed: `8 passed, 0 skipped`.
- Real transaction overlap proved `SELECT FOR UPDATE SKIP LOCKED`: one queued job and two concurrent claim attempts produced exactly one `CLAIMED` job and one no-job response.

## Real Provider And Chat E2E

The direct `LocalWorkerProvider` path created a durable `inference_jobs` row, was claimed by `home-5090-01`, generated through llama.cpp, and completed as `SUCCEEDED`.

- Result engine: `llama_cpp`
- Result model: `Qwen/Qwen3.5-9B`
- Result revision: `c202236235762e1c871ad0ccb60c8ee5ba337b9a`
- Baseline successful job: llama inference `420 ms`, provider total `1500 ms`

The real `/api/chat` E2E passed with an active local Persona and confirmed Memory fixture. The user message did not contain the fixture code. The Cloud ContextBuilder final messages contained the fixture marker, and the exact final message list was persisted in the job request payload.

- Chat runtime mode: `local`
- Chat provider: `local-worker`
- Job status: `SUCCEEDED`
- Worker claimant: `home-5090-01`
- One combined leading system message was used for Qwen chat-template compatibility.
- Context marker and assistant response were verified as `\u9752\u7af99037`.
- Session, user message, assistant message, InteractionTrace, request ID, and trace-to-job relationship were persisted and verified.

## No Fallback, Failure, And Recovery

`local_worker` mode constructs `LocalWorkerProvider` and leaves `ExternalOpenAIProvider` absent. The local Core ran with dummy unusable external LLM settings.

After managed llama.cpp shutdown:

- Worker transitioned to `DEGRADED` and cleared `loaded_model`.
- Local provider request failed clearly rather than succeeding through a fallback.
- The claimed failure job recorded `MODEL_UNAVAILABLE`.

After managed llama.cpp restart, without restarting the Worker:

- Worker returned to `ONLINE` with its model metadata.
- A new structured durable job completed as `SUCCEEDED` through llama.cpp.

## Durable Failure Handling

On the isolated PostgreSQL E2E database:

- A selected but non-claiming test Worker caused provider timeout and its job became `EXPIRED`.
- Completion with an expired claim token was rejected and did not overwrite the final state.
- Cleanup removed an old terminal job while retaining a recent queued job.

## Regression And Qualification

- T08-4 focused routing/API/provider/Worker/chat/readiness tests passed.
- Applicable full suite passed with the existing `tests/test_deployment_assets.py` Windows/WSL path harness excluded.
- The deployment harness issue remains pre-existing from accepted T08-2: WSL `bash -n` receives a Windows path and exits `127`; no skip or xfail was added.
- No service token, local model key, external provider key, SSH key, or private path is recorded here.