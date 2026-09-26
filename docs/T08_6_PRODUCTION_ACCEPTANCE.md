# T08-6 Production Failover Acceptance

Date: 2026-09-26

## Production Topology

- Implementation and running image: `277fda1` / `localhost/inaba-core:277fda1`.
- Active production Core: `127.0.0.1:1516`, provider mode `prefer_local_with_cloud_fallback`.
- Rollback Core: `127.0.0.1:1515`, provider mode `cloud`.
- Nginx public route: `/tewi` proxies to `127.0.0.1:1516`.
- Database revision: `20260925_0006`.
- PostgreSQL container identity, `StartedAt`, and PID were unchanged throughout the final toggle and acceptance run. PostgreSQL restart: **NO**.

## Production Verification

The no-worker fallback baseline, Windows A-E production smoke, and final toggle
run `T08PROD-20260926214447-0dfea877` passed. No prompts, response bodies,
memory content, credentials, or tokens are recorded in this document.

| Phase | Windows end-to-end latency | Persisted provider outcome | Result |
| --- | ---: | --- | --- |
| P1 worker online | 2110 ms | `local-worker`; local job succeeded | PASS |
| P2 worker offline | 3320 ms | `WORKER_UNAVAILABLE` then one `external-openai` fallback; no local job | PASS |
| P3 worker restored | 3472 ms | `local-worker`; local job succeeded | PASS |

- P1 and P3 local jobs were each claimed by `home-5090-01` and recorded engine
  `llama_cpp`, model `Qwen/Qwen3.5-9B`, revision
  `c202236235762e1c871ad0ccb60c8ee5ba337b9a`.
- Trace provider durations (metadata total latency): P1 `1697 ms`, P2 `2963 ms`,
  P3 `3114 ms`; these are informational baselines for T08-7.
- P1 and P3 have `fallback_attempted=false` and `final_provider=local-worker`.
- P2 has `primary_provider=local-worker`,
  `primary_failure_kind=WORKER_UNAVAILABLE`, `fallback_attempted=true`,
  `fallback_provider=external-openai`, and `final_provider=external-openai`.
  Provider-level Cloud fallback attempts: exactly one.
- The P2-to-P3 transition proves `sticky_cloud_after_fallback=NO`.
- Each of P1, P2, and P3 persisted exactly one user message and one assistant
  message. P1 and P3 each had exactly one local job; P2 had none.

## Final Serving State

- Both Core containers are running, loopback-bound, and report their required
  live and ready health endpoints as HTTP `200`; `1516` remains serving with the
  worker unavailable through Cloud fallback.
- The retained worker registry record `home-5090-01` was not deleted. Its stored
  heartbeat is stale, so its runtime effective status is `OFFLINE`.
- Active inference jobs after validation and cleanup: `QUEUED=0`, `CLAIMED=0`.
- The original cloud Core on `1515` remains healthy and ready for rollback.

## Cleanup and Rollback

- After all evidence was recorded, P1/P2/P3 acceptance sessions, messages,
  traces, and the two exact local inference jobs were removed by fixed IDs in
  scoped transactions. No broad delete or truncate was used; no acceptance
  memory, feedback, or training-candidate records existed.
- Cleanup verification confirmed all three sessions, messages, traces, and
  related jobs are absent.
- Rollback remains available: after validating Nginx configuration, change only
  the `/tewi` upstream from `127.0.0.1:1516` to `127.0.0.1:1515` and reload
  Nginx. The rollback Core continues to use Cloud mode.

T08-6 PRODUCTION PASS

- Local-first: ENABLED
- Automatic Cloud fallback: ENABLED
- Local recovery: VERIFIED