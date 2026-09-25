# T08-3 Local Inference Acceptance

**Status: `PASS`**

## Passed

- Official Qwen source revision is pinned and self-converted to F16 GGUF; no third-party GGUF is used.
- Native Windows CUDA llama.cpp recognizes the RTX 5090 and serves the authenticated OpenAI-compatible API on loopback port `18081`.
- `/v1/models`, text chat completion, exact `AgentResponse` JSON Schema output, Pydantic validation, and the existing `LocalModelClient` all pass.
- `LocalModelClient` health returns `True`; structured generation returns a valid `AgentResponse`.
- Registry metadata code reports `loaded_model=Qwen/Qwen3.5-9B`, the fixed revision, and `model_alias=local-dev` when the local model is healthy.
- Live local lifecycle verification passed: stopping only the managed llama.cpp process changed `WorkerRuntime` to `DEGRADED` with null model metadata; restarting the server without restarting the Worker runtime restored `ONLINE` and all pinned model metadata.
- Unit tests cover online/degraded Worker behavior, manifest pinning, loopback-only scripts, state-scoped stopping, and client payload compatibility.
- Attempt 3 on 2026-09-25 passed after the user restored the `AliyunServer` SSH identity and agent configuration. BatchMode authentication returned `__SSH_OK__`; the owned tunnel bound only `127.0.0.1:18151`; `/health/live` and `/health/ready` both returned HTTP `200`.
- The Worker registered as `home-5090-01` with `ONLINE`, `NVIDIA GeForce RTX 5090`, `llm.inference`, `loaded_model=Qwen/Qwen3.5-9B`, the pinned source revision, and `model_alias=local-dev`.
- The Registry contained exactly one `home-5090-01` record. A subsequent heartbeat advanced the timestamp while the model metadata remained present.
- With the Worker still running, stopping the managed llama-server made the Cloud Registry report `DEGRADED` with null model metadata. The heartbeat timestamp advanced and the Registry still contained one Worker.
- Restarting llama-server restored `/v1/models`, then restored Cloud `ONLINE` plus all model metadata without changing the Worker PID. A final `LocalModelClient.generate()` returned a nonempty response.
- The Worker, llama-server, and owned tunnel were stopped normally after acceptance. Cloud-to-local inference was not enabled.

## Historical Blocks

Attempt 1: the local health request returned HTTP `200`, then `POST http://127.0.0.1:18151/api/workers/register` returned HTTP `502`. Inspection found no listener on `127.0.0.1:18151`; the existing SSH tunnel was absent.

Attempt 2 on 2026-09-25: a new tunnel attempt using `AliyunServer` did not create the required loopback listener. A non-interactive SSH command through that alias returned `Permission denied (publickey,password,keyboard-interactive)`. The owned SSH process was stopped and port `18151` was verified clear.

A subsequent read-only SSH recovery check found that `AliyunServer` has an IdentityFile configured, but that file is missing. The Windows `ssh-agent` service is disabled and `ssh-add -l` has no agent socket. No replacement key was generated, no SSH configuration or service policy was changed, and no Worker was started.

## Regression Qualification

The applicable T08-3 suite passes. Full discovery retains one known pre-existing Windows/WSL harness issue: `test_production_scripts_are_shell_valid_and_never_prune_or_remove_volumes` passes a Windows `Path` to WSL `bash -n`, which exits `127`. It reproduces identically at T08-2 accepted commit `c9483c2`; it is classified as `PRE-EXISTING / NOT T08-3 REGRESSION` and was not hidden with skip or xfail.