# T08-3 Local Model Inference

The current production-candidate local runtime is native Windows CUDA `llama.cpp`. It exposes an authenticated OpenAI-compatible endpoint only at `http://127.0.0.1:18081/v1`; it never binds a LAN address. The committed manifest is `configs/local_models/qwen3.5-9b-llamacpp.yaml`, which pins the Hugging Face source revision, self-converted F16 GGUF hash, llama.cpp build, and matching CUDA runtime artifact.

The prior vLLM manifest is retained at `configs/local_models/qwen3.5-9b.yaml` as a blocked experimental option. Model weights, GGUF artifacts, runtime binaries, state files, API key files, and logs are outside the repository.

## Operations

Set a non-placeholder `INABA_LOCAL_LLM_API_KEY` in ignored `.env.worker`, then run the selected runtime:

```powershell
.\scripts\local_model\start_llamacpp.ps1
.\scripts\local_model\status_llamacpp.ps1
```

`start_llamacpp.ps1` starts only the configured `llama-server.exe`, uses an API key file rather than a key command-line argument, binds exactly to loopback, and records a scoped state file. Its stop script verifies the executable and GGUF identity before stopping the recorded PID:

```powershell
.\scripts\local_model\stop_llamacpp.ps1
```

The preserved vLLM operations are:

```powershell
.\scripts\local_model\start_vllm.ps1
.\scripts\local_model\status_vllm.ps1
```

The startup scripts do not start the Worker.

The Worker is an outbound health reporter only. Set `INABA_LOCAL_LLM_ENABLED=true` and the local API settings in `.env.worker`; it reports `ONLINE` with model metadata when `/v1/models` contains the configured model, and `DEGRADED` with null model metadata when the local runtime is unavailable. Cloud chat routing is unchanged.

## vLLM Runtime Status

**Status: `BLOCKED_RUNTIME_COMPATIBILITY`**

The `vllm` runtime remains an experimental runtime option and is preserved for later reassessment. Its scripts (`start_vllm.ps1`, `stop_vllm.ps1`, and `status_vllm.ps1`) must not be deleted.

The following non-sensitive smoke was reproduced on 2026-09-24:

- Runtime: Docker Desktop with WSL2 Linux backend
- GPU: NVIDIA GeForce RTX 5090, SM120 (compute capability 12.0)
- vLLM: 0.30.0
- CUDA runtime: 13.0
- Model: `Qwen/Qwen3.5-9B` at revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a`
- Attempts: default startup and `--enforce-eager`

In both attempts, model weights loaded and used approximately 22.2 GB VRAM. The `EngineCore` process remained alive, there was no OOM, restart, crash, or error traceback, but the engine never became ready. `/v1/models` closed the connection and the EngineCore/API threads remained waiting. This is not classified as an ordinary model-load failure. The unready container was stopped.

Current runtime selection is `runtime=llama_cpp`; `runtime=vllm` is recorded as `blocked_on_rtx5090_wsl2`.