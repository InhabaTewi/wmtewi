# T08-2 Local GPU Worker Live Smoke

Date: 2026-09-25

## Environment

- Branch: `t08-gpu-worker`
- Implementation commit: `c9483c2`
- Worker ID: `home-5090-01`
- GPU: `NVIDIA GeForce RTX 5090`
- VRAM total: `32607 MB`
- Transport: local `127.0.0.1:18151` SSH tunnel to Cloud Core `127.0.0.1:1515`
- Local integration database: PostgreSQL with pgvector at `127.0.0.1:55432`

## Results

- Cloud live and readiness checks through the SSH tunnel returned HTTP 200.
- Worker `--diagnose` detected the RTX 5090 and expected VRAM.
- Worker `--once` registered and heartbeated successfully with bearer authentication.
- Cloud Registry reported `ONLINE`, one `home-5090-01` record, `llm.inference`, RTX 5090 metadata, VRAM greater than 30000 MB, and `loaded_model: null`.
- After the one-shot worker exited and the Cloud heartbeat window elapsed, Cloud computed `effective_status: OFFLINE` while preserving the reported status.
- A daemon worker re-registered the same Worker ID, restored `ONLINE`, and retained a single registry record.
- Two daemon heartbeats were observed with increasing `last_heartbeat_at` values.
- The detached Windows daemon was stopped through its local shutdown-file trigger. The process exited, closed its HTTP client through the runtime shutdown path, and Cloud later computed `effective_status: OFFLINE`.
- A final daemon restart restored `ONLINE` with the same single Worker ID, then shut down and timed out to final `OFFLINE`.

## Boundaries

The smoke used only Worker Registry endpoints: register, heartbeat, worker detail, and worker list. It did not create or modify Persona, Memory, Knowledge, Chat Session, Message, inference, model, or training data.

No service token, SSH key, Cloud credential, database credential, or provider key is recorded in this document. `.env.worker` remains Git ignored.

## Regression

The complete local suite ran with the isolated pgvector integration database enabled:

```text
116 passed, 0 failed, 0 skipped
```