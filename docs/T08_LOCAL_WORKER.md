# T08-2 Local GPU Worker

## Scope

The local GPU Worker is an outbound Windows or WSL2 process. It reads local configuration, probes GPU metadata through `nvidia-smi`, registers with Cloud Core, and sends heartbeats. It does not access PostgreSQL, run a web server, load a model, perform inference, or schedule jobs.

## Prerequisites

- Python 3.12 or newer with the project dependencies installed.
- NVIDIA drivers that provide `nvidia-smi` on `PATH` when GPU metadata is required.
- A reachable Cloud Core URL and valid service token.

Create the private configuration file from `.env.worker.example`:

```powershell
Copy-Item .env.worker.example .env.worker
```

`.env.worker` is ignored by Git. Do not commit a service token.

## Configuration

`INABA_CLOUD_BASE_URL` is the only Cloud endpoint setting. The Worker joins it safely with `/api/workers/register` and `/api/workers/{worker_id}/heartbeat`; it may include a reverse-proxy prefix such as `/tewi`.

Use a local or SSH-tunnel URL during development. The public production endpoint currently uses HTTP, so do not send a real service token directly to it. A tunnel may expose Cloud Core locally without its reverse-proxy prefix:

```powershell
ssh -N -L 18151:127.0.0.1:1515 root@cloud-host
```

Then set `INABA_CLOUD_BASE_URL=http://127.0.0.1:18151`. Keep the service token only in `.env.worker`; never paste it into chat or source control.

The default heartbeat interval is 10 seconds. Cloud Core may return a replacement interval; the Worker accepts only values between 5 and 60 seconds.

## Commands

Validate configuration, URL, and GPU access without contacting Cloud Core:

```powershell
python -m apps.gpu_worker --diagnose
```

Register, send one heartbeat, and exit:

```powershell
python -m apps.gpu_worker --once
```

Run the outbound daemon until `Ctrl+C`, `SIGINT`, or `SIGTERM`:

```powershell
python -m apps.gpu_worker
```

The Worker reports `REGISTERING` during registration, then `ONLINE` after a successful GPU probe or `DEGRADED` when the probe fails. It declares `llm.inference` as future node capability, but does not load a model. `loaded_model`, model version, and alias remain `null`. The deployed T08-1 protocol identifies a node by `worker_id`; `INABA_WORKER_NAME` is retained for local logs and diagnostics until Cloud Core gains a versioned display-name field.

## Connectivity and Offline Behavior

Network failures use bounded exponential retry with jitter, capped at 30 seconds. On recovery the Worker re-registers before resuming heartbeats. An initial registration `401` or `404` exits nonzero because it indicates invalid credentials or an incompatible Cloud protocol. A later `401` also stops the Worker.

When the Worker exits, Cloud Core eventually computes the worker as `OFFLINE` after its configured heartbeat timeout. No explicit offline write is required.

## Troubleshooting

- `nvidia-smi` missing, timeout, malformed output, or an unavailable GPU index leaves the Worker running as `DEGRADED`.
- Verify the selected card with `INABA_GPU_INDEX`; the probe supports multi-GPU hosts.
- `--diagnose` prints the worker ID, Cloud host, selected GPU, and VRAM only. It never prints the service token.
- A `401` means the configured service token is not accepted. A `404` usually means the Cloud Core version or base URL is incompatible.