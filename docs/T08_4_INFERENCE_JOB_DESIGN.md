# T08-4 Cloud To Local Inference Job Design

## Scope

T08-4 adds durable, Cloud-owned inference jobs for an outbound-only Worker. It does not add a Cloud-to-Windows listener, public endpoint, fallback to the external provider, worker access to Cloud persistence, or a production deployment.

The request path is:

```text
Cloud ChatService / ContextBuilder
  -> LocalWorkerProvider
  -> inference_jobs (PostgreSQL)
  -> Worker claim API
  -> LocalModelClient / llama.cpp
  -> Worker complete or fail API
  -> LocalWorkerProvider
  -> existing ChatService persistence
```

Persona, session history, memory, knowledge retrieval, context assembly, response validation, conversation persistence, and trace ownership remain in Cloud Core. The Worker receives only final OpenAI-compatible messages and response-schema metadata.

## Provider Mode

`LLM_PROVIDER_MODE` is an explicit Cloud configuration with exactly two values:

- `cloud` (default): use `ExternalOpenAIProvider`.
- `local_worker`: use `LocalWorkerProvider` only.

`local_worker` never calls `ExternalOpenAIProvider`; unavailable workers, model failure, timeout, or invalid output become stable local-provider errors. This deliberately differs from the existing health-routing behavior and establishes the no-fallback baseline for later work.

## Job Model And Retention

`inference_jobs` persists transient inference payloads, which can contain assembled conversation, memory, and knowledge text. Such payloads are not written to ordinary logs.

The job includes UUID job/request/trace IDs, target worker and model constraints, request/result JSON payloads, stable error details, claim token, lease/expiry timestamps, and lifecycle timestamps. Indexed access paths cover status plus target worker, creation, and expiration. A cleanup service removes terminal jobs older than the configured retention interval; T08-4 does not add a scheduler.

## State Machine And Lease

```text
QUEUED -> CLAIMED -> SUCCEEDED
                  -> FAILED
QUEUED -> EXPIRED
CLAIMED -> EXPIRED
```

Cloud creates a `QUEUED` job for an eligible, ONLINE `llm.inference` Worker. Worker claim uses one transaction and `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL, followed by the `CLAIMED` update, random claim-token generation, and lease assignment. SQLite unit tests cover sequential non-duplication; PostgreSQL concurrent-claim verification requires a configured dedicated test database.

`complete` and `fail` require matching `job_id`, `worker_id`, claim token, `CLAIMED` state, and an unexpired lease. A stale, wrong-worker, duplicate, or expired submission cannot overwrite the job. Expired leases become `EXPIRED`; T08-4 never requeues them because inference is not strictly idempotent.

## APIs And Worker Pull

All APIs remain under existing bearer service-token protection:

- `POST /api/inference/jobs/claim`: returns at most one compatible job or immediate `204`; the Worker uses a bounded idle polling interval.
- `POST /api/inference/jobs/{job_id}/complete`: submits validated structured output.
- `POST /api/inference/jobs/{job_id}/fail`: submits a stable, non-sensitive failure code.

Workers cannot list jobs, inspect prompt history, or submit results for another worker. The job loop runs independently of the heartbeat loop and claims only while its local model probe is healthy. It has fixed concurrency one and a bounded idle poll interval; failure for one job is reported to Cloud and does not end heartbeats or the Worker process.

## Contracts, Validation, And Errors

Shared Pydantic schemas define the request payload, claim, success result, failure result, status, and stable errors: `WORKER_UNAVAILABLE`, `MODEL_UNAVAILABLE`, `JOB_EXPIRED`, `INFERENCE_TIMEOUT`, `INFERENCE_ERROR`, `INVALID_RESULT`, and `MODEL_MISMATCH`.

The request carries final messages, response schema name/schema, and only needed generation settings. The Worker validates model alias/version constraints, calls the existing `LocalModelClient`, validates the structured `AgentResponse`, and submits the result. Cloud validates result payload again before `LocalWorkerProvider` returns it. Usage/timing fields may be null when llama.cpp does not return them; they are never fabricated.

## Trace, Security, And Observability

ChatService's trace ID becomes the job trace ID. Job ID, trace ID, worker ID, status transitions, model alias/version, and latency may be logged; prompts, context payloads, claim tokens, secrets, and private paths may not. Live smoke continues to use `127.0.0.1:18151` through the SSH tunnel, never public HTTP to the Worker.

## Verification Plan

Tests cover persistence, concurrent atomic claim, lease rejection, expiration, cleanup, protected APIs, worker job-loop behavior, local-provider no-fallback behavior, and ContextBuilder-to-final-messages boundary. PostgreSQL integration covers migration `0005 -> 0006`, fresh-to-head, downgrade, re-upgrade, and concurrent claim. The final local E2E runs real native llama.cpp, an outbound Worker, and local Cloud Core; production deployment remains pending.

## Local Acceptance

Local Windows acceptance completed on 2026-09-26. It verified real PostgreSQL migration and concurrent claim behavior, native RTX 5090 llama.cpp inference, outbound Worker claim/complete behavior, direct provider and full chat flow, Cloud ContextBuilder payload ownership, no external LLM fallback, model-down failure, Worker recovery without restart, timeout expiry, stale claim rejection, and cleanup. See `docs/T08_4_LOCAL_E2E_ACCEPTANCE.md` for non-sensitive evidence and measured baseline latency.