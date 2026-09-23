# T08-1 Worker Protocol Design

## Scope

T08-1 establishes the Cloud Core registry for pluggable GPU compute nodes. It implements worker identity, registration, heartbeat, status reporting, and protected diagnostics APIs. It does not implement inference jobs, job claims, result delivery, a scheduler, WebSockets, a local Windows worker program, CUDA, model serving, or Provider Router changes.

## Architecture

Workers are infrastructure nodes, not sources of business truth. Persona, Memory, Knowledge, conversations, Provider API keys, database credentials, and the service token never belong in the Worker Registry.

The network direction is worker-to-cloud. A worker uses HTTPS to register and periodically post a heartbeat to Cloud Core. Cloud Core never needs to connect to a residential public IP, which avoids NAT, dynamic IP, and home-network ingress requirements.

```text
GPU Worker -- HTTPS register/heartbeat --> Cloud Core API --> PostgreSQL worker_nodes
```

## Existing Schema and T08-1 Extension

`worker_nodes` already provides `id`, `node_name`, `status`, `capabilities`, timestamps, and `last_heartbeat_at`. T08-1 reuses those infrastructure fields and adds a protocol identity plus metadata that is required by the registry:

- `worker_id`: stable protocol identity, independent of any user, session, or persona.
- `last_seen_at`: server receipt time for the most recent registration or heartbeat.
- GPU metadata: name, count, total/used/free VRAM in MB.
- model metadata: loaded model, version, and alias.

Migration `0005` adds only these fields and indexes. It does not change migrations `0001` through `0004`. Existing rows receive a `worker_id` derived from `node_name` before the column becomes required.

## Registration

`POST /api/workers/register` accepts a `WorkerRegisterRequest` with a `worker_id`, capabilities, optional status, GPU metadata, and model metadata.

Repeated registration is an upsert by `worker_id`: the existing node is updated rather than duplicated. Cloud Core returns `WorkerRegisterResponse` with the persisted `WorkerInfo`, `heartbeat_interval_seconds`, server time, and effective configuration. No credential, provider key, or database setting is returned.

Example:

```json
{
  "worker_id": "home-5090-01",
  "status": "ONLINE",
  "capabilities": ["llm.inference"],
  "gpu_name": "RTX 5090",
  "gpu_count": 1,
  "vram_total_mb": 32768,
  "loaded_model": "qwen-base",
  "model_version": "base",
  "model_alias": "development"
}
```

## Heartbeat

`POST /api/workers/{worker_id}/heartbeat` accepts `WorkerHeartbeatRequest`. It updates worker status, capabilities, GPU/model metadata, `last_heartbeat_at`, and `last_seen_at`. The operation is idempotent: posting the same body more than once results in one worker record with the newest server timestamps.

The heartbeat request never contains a database URL, service token, Persona, Memory, Knowledge, or conversation content.

## Status Machine

The protocol supports these reported states:

- `REGISTERING`
- `ONLINE`
- `BUSY`
- `TRAINING`
- `DEGRADED`
- `OFFLINE`

Workers report their operational state. Cloud computes `effective_status` when reading a node: if the worker has not heartbeated within `WORKER_HEARTBEAT_TIMEOUT_SECONDS` (default 45), it is considered `OFFLINE` without requiring a background scheduler or a database write. A fresh heartbeat restores the reported state.

## Capabilities

The initial capability vocabulary is:

- `llm.inference`
- `llm.training`
- `asr`
- `tts`
- `vision`

T08-1 tests use only `llm.inference`. Capabilities describe node support; they do not schedule work.

## Security

All Worker Registry endpoints are under `/api/workers` and are protected by the existing M01 `Authorization: Bearer SERVICE_TOKEN` middleware. Health endpoints remain anonymous. The first version uses the shared service token; per-worker credentials are a future security improvement.

Worker list and detail responses expose only infrastructure metadata and never echo the bearer token.

## Query APIs

- `GET /api/workers`
- `GET /api/workers/{worker_id}`

These protected endpoints return `WorkerInfo`, including dynamic `effective_status`, for diagnostics, a control panel, and a future Provider Router integration.

## Future Job Protocol

T08-2 and later may add worker job polling, job claim, progress, completion, inference routing, local model serving, and per-worker credentials. Those additions must preserve the worker-to-cloud direction and keep Cloud Core authoritative for business data.