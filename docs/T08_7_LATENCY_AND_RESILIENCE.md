# T08-7 Latency and Resilience Hardening

## Scope

This change is local source, tests, tooling, and documentation only. It does not deploy Core, change Nginx, modify production environment files, run a migration, restart PostgreSQL, or contact production.

The intent is to measure before tuning. Existing production acceptance numbers remain the baseline:

| Scenario | Total chat latency | Local/primary latency | Result |
| --- | ---: | ---: | --- |
| T08-6.1D local | about 2243 ms | about 1843 ms | local |
| T08-6.1D model unavailable | about 20806 ms | about 5 ms primary; about 20220 ms Cloud | Cloud fallback |
| T08-6.1D Worker offline | about 6080 ms | about 5 ms primary; about 5387 ms Cloud | Cloud fallback |
| Final P1 local | about 2110 ms | not separately retained | local |
| Final P2 Worker offline | about 3320 ms | not separately retained | Cloud fallback |
| Final P3 local | about 3472 ms | not separately retained | local |

Known unavailable Worker/model detection was fast. The roughly 20-second model-unavailable case was Cloud generation time, not a local timeout. No Cloud timeout has been shortened from these observations.

## Trace Timing Contract

`InteractionTrace.payload["timing"]` contains non-sensitive measurements only. All durations use a monotonic `perf_counter` clock in their process.

| Field | Meaning | Availability |
| --- | --- | --- |
| `local_worker_selection_ms` | Core local route/Worker selection duration | measured |
| `job_create_ms` | Core durable inference-job creation duration | measured for LocalWorkerProvider |
| `job_queue_ms` | Core-observed wait from job creation to first `CLAIMED` poll | measured after claim, otherwise null |
| `job_claim_ms` | Exact cross-process claim RPC duration | null; not reliably measurable with current protocol |
| `local_inference_ms` | Worker-provided local model inference duration | measured when Worker result provides it |
| `local_result_commit_ms` | Exact Worker result-commit duration | null; not reliably measurable with current protocol |
| `knowledge_retrieval_ms` | Chat knowledge search | measured |
| `memory_retrieval_ms` | Memory-only lookup separated from ContextBuilder | null; current builder combines work |
| `context_build_ms` | Context construction | measured |
| `primary_duration_ms` | First provider attempt | measured |
| `failover_decision_ms` | Pure typed failure policy decision | measured |
| `fallback_duration_ms` | Cloud attempt after eligible local failure | measured when attempted |
| `total_provider_ms` | Provider execution total | measured |
| `total_chat_ms` | Chat method elapsed before persistence commit | measured |

The trace intentionally does not add prompt text, generated text, memory content, knowledge content, keys, headers, or endpoints to the timing object.

## Timeout Semantics

The prior single local-worker request timeout combined two operationally different failures. Settings are now independently configurable:

- `LOCAL_JOB_CLAIM_TIMEOUT_SECONDS`: default 5 seconds. Applies only after an eligible ONLINE Worker and model are selected but the durable job is not claimed.
- `LOCAL_INFERENCE_TIMEOUT_SECONDS`: default 120 seconds. Starts when Core first observes `CLAIMED`; applies while awaiting a terminal result.
- `CHAT_TOTAL_TIMEOUT_SECONDS`: default 180 seconds. Recorded as an explicit budget configuration for the next enforcement design; it is not yet used to abort a request, so it cannot unexpectedly cut off existing Cloud generation.

Worker/model unavailable remains a pre-job typed failure: `WORKER_UNAVAILABLE` or `MODEL_UNAVAILABLE`. No durable job is created, so local-first fallback can begin Cloud immediately.

An unclaimed eligible job expires as `JOB_QUEUE_TIMEOUT`. A claimed but non-terminal job expires as `LOCAL_INFERENCE_TIMEOUT`. Both are fallback eligible. The 5-second initial claim budget is conservative relative to the Worker one-second claim polling interval and leaves normal scheduling/transport headroom; it should be revisited after local benchmark data is captured.

## Measurement Commands

The local endpoint benchmark defaults to one warm-up request and ten measured requests:

```bash
python scripts/local_model/benchmark_local_llm.py
```

For any explicitly authorized non-production OpenAI-compatible provider:

```bash
python scripts/benchmark_provider_latency.py --base-url URL --model MODEL
```

The generic benchmark reports measured non-streaming end-to-end duration, including mean, p50, p95, minimum, and maximum. It reports `connect_dns_tls_ms` and `ttft_ms` as `null`: a buffered non-streaming API cannot distinguish those phases reliably. Neither benchmark prints its request prompt or API key. No benchmark was run against production as part of this task.

## Core-Only Deployment Guard

`scripts/update_core_only.sh` is the follow-up Core update workflow. It is intentionally distinct from `deploy_prod.sh`, which retains first-deployment behavior and explicitly starts PostgreSQL.

The Core-only workflow:

1. Requires PostgreSQL to already be running and records its container ID and `StartedAt`.
2. Builds or selects only the Core image.
3. Runs Alembic as a no-dependency one-off Core container.
4. Recreates only Core with `up -d --no-deps --force-recreate core`.
5. Checks Core live and ready endpoints.
6. Re-reads PostgreSQL container ID and `StartedAt`; any change is an explicit failure/alarm.

It contains no PostgreSQL `up`, `down`, volume, restore, or automatic rollback operation. This workflow has not been run against production. Its script contract has focused static coverage; the current Windows environment cannot run the pre-existing `bash -n` asset check because WSL mangles the Windows path before any script is parsed.

## Residual Risks and Next Measurements

- Local queue timing is Core-observed polling latency, not an exact Worker claim timestamp.
- The Worker protocol does not currently carry monotonic claim and result-commit phase timings.
- The non-streaming Cloud client cannot provide TTFT or split DNS/TLS without a streaming/transport instrumentation change.
- `chat_total_timeout_seconds` should be enforced only after measuring Cloud tail latency and deciding whether a terminal Cloud timeout should be surfaced or be retried.
- Production blue/green Core rollback remains the accepted active/rollback topology; Core-only script validation must first be done in a disposable compose project before being used for a production rollout.
