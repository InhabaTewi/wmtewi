# T08-5 Failover Semantics

## Scope

T08-5 defines failure classification and fallback eligibility. It does not execute cloud fallback, add provider modes, change production configuration, or deploy production changes.

## Typed Failure Contract

`ProviderFailure` carries a stable `kind`, provider name, retryable flag, fallback eligibility, public error code, safe message, and optional internal cause. Causes are not included in ordinary user responses or INFO logs.

`FailoverPolicy` is pure and deterministic. It performs no network, database, or provider operation. A decision that fallback is allowed is classification only; actual fallback remains a T08-6 responsibility.

## Taxonomy And Policy

| Failure kind | Retryable | Fallback eligible |
| --- | --- | --- |
| `WORKER_UNAVAILABLE` | yes | yes |
| `MODEL_UNAVAILABLE` | yes | yes |
| `JOB_QUEUE_TIMEOUT` | yes | yes |
| `JOB_LEASE_EXPIRED` | yes | yes |
| `LOCAL_INFERENCE_TIMEOUT` | yes | yes |
| `LOCAL_INFERENCE_ERROR` | yes | yes |
| `MODEL_MISMATCH` | no | no |
| `INVALID_STRUCTURED_RESULT` | no | no |
| `PROVIDER_AUTH_ERROR` | no | no |
| `PROVIDER_CONFIGURATION_ERROR` | no | no |
| `INVALID_REQUEST` | no | no |
| `STORAGE_ERROR` | no | no |

`MODEL_MISMATCH` is never silently redirected because an explicitly requested model version cannot be substituted. `INVALID_STRUCTURED_RESULT` is not eligible in this version because duplicate generation across models is not predictable.

## Local Worker Mapping

Local Worker selection, model availability, durable job timeout or expiry, worker-reported error codes, result validation, and storage errors are mapped to `ProviderFailure`. The public local-mode unavailable behavior remains compatible with T08-4.

Provider warning logs include only `provider`, `failure_kind`, `fallback_eligible`, and `trace_id`; prompts, memory, knowledge, job payloads, and secrets are excluded.

## T08-6 Boundary

Both `cloud` and `local_worker` modes retain their T08-4 behavior. In particular, an eligible failure in `local_worker` mode still fails locally and does not call `ExternalOpenAIProvider`. T08-6, if accepted later, is responsible for any explicit fallback execution policy and user-visible behavior.