# T08-6 Prefer Local With Cloud Fallback

## Scope

T08-6 adds `prefer_local_with_cloud_fallback` alongside the unchanged `cloud` and `local_worker` provider modes. The default remains `cloud`. This mode is implemented and locally tested only; it does not change production configuration or deploy production artifacts.

## Request Lifecycle

`ChatService` retrieves knowledge, builds one `ContextBuilder` result, and creates final messages before provider generation. The local-first wrapper receives those final messages and the same response schema for both attempts. It never rebuilds Persona, Memory, Knowledge, or conversation context.

```text
ChatService / ContextBuilder (once)
  -> LocalWorkerProvider
  -> success: persist one final response
  -> eligible ProviderFailure: ExternalOpenAIProvider (once)
  -> persist one final response
```

The maximum provider attempts for one request is two: local first and, only when allowed, one Cloud attempt. There is no circuit breaker, no local multi-Worker sequence, and no Cloud-to-local retry.

## Policy

`ProviderFailure` and the T08-5 `FailoverPolicy` are the only fallback authority. Eligible failures are `WORKER_UNAVAILABLE`, `MODEL_UNAVAILABLE`, `JOB_QUEUE_TIMEOUT`, `JOB_LEASE_EXPIRED`, `LOCAL_INFERENCE_TIMEOUT`, and `LOCAL_INFERENCE_ERROR`.

`MODEL_MISMATCH`, `INVALID_STRUCTURED_RESULT`, provider authentication/configuration errors, invalid requests, and storage errors do not fall back. Unknown exceptions are not caught for fallback and therefore fail closed.

## Persistence And Stale Results

Chat persistence occurs only after the final successful provider result, producing one user message, one assistant message, and final-result memory side effects once. A failed local attempt produces no assistant message or memory side effect.

Local timeout first moves the durable job to `EXPIRED`. The existing lease/token checks reject a late local completion, so it cannot overwrite the Cloud fallback result or create another assistant message.

## Trace And Readiness

Successful traces record a non-sensitive provider path, primary provider/duration, optional primary failure kind, fallback attempted/provider/duration, and final provider. If the Cloud fallback fails with a known provider error, a failure trace records those attempt fields and the Cloud error type; it contains no prompt, memory, knowledge, payload, or secret.

In prefer-local mode readiness is healthy when both providers are healthy, degraded but still serving when either one is healthy, and unavailable only when both are unavailable. `cloud` and `local_worker` readiness behavior remains unchanged.

The configured sequential request budgets are the existing Local Worker request timeout (default 120 seconds) followed, only after an eligible local failure, by External OpenAI connect/read timeouts (defaults 10/60 seconds). T08-6 adds no outer timeout or hidden retry loop; Cloud HTTP retries remain the existing provider setting.

## Production Boundary

Automatic fallback is not enabled in production until a separate deployment and production acceptance. T08-6 does not change the production provider mode.