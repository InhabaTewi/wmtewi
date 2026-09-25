# T08-3R Local Runtime Baseline

## vLLM

**Status: `BLOCKED_RUNTIME_COMPATIBILITY`**

The preserved `vllm/vllm-openai` implementation ran in Docker Desktop's WSL2 backend on RTX 5090 SM120 with vLLM 0.30.0 and CUDA 13.0. Both default startup and `--enforce-eager` loaded the pinned Qwen weights, used about 22.2 GB VRAM, and kept EngineCore alive, but the API never became ready: `/v1/models` closed connections and the runtime threads waited without an OOM, restart, crash, or traceback. It remains `runtime=vllm`, `status=blocked_on_rtx5090_wsl2`.

## Native llama.cpp

**Status: `LOCAL_INFERENCE_BASELINE = PASS`**

- Source model: `Qwen/Qwen3.5-9B`
- Hugging Face revision: `c202236235762e1c871ad0ccb60c8ee5ba337b9a`
- Source verification: `model.safetensors.index.json` references four local shards; aggregate shard bytes: `19,306,310,880`
- Converter: official `convert_hf_to_gguf.py` from llama.cpp commit `7fe450e19305b828c199d602c23a8337aaa1f03b`
- GGUF: F16, `442` tensors, `18,407,321,440` bytes, SHA256 `88fbfd78c7e5e74b01ad7868e93f7779feff3101b02dabc5e33e23a7fa2b78ff`
- Runtime: official Windows CUDA build `b11146`, version `0.5.0-dev (build 11146, commit 7fe450e19)`
- Binary artifact SHA256: `b1866c0ce76bc7bfb0c24b33e9a37e9669f1be18539b12c74ce361f81c41f047`
- CUDA runtime companion SHA256: `738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668`
- CUDA device probe: `CUDA0: NVIDIA GeForce RTX 5090 (32606 MiB, 30841 MiB free)` before model load
- Server: `llama-server.exe` bound to `127.0.0.1:18081`, authenticated `/v1/models` returned `inaba-local-qwen`
- OpenAI-compatible smoke: `/v1/chat/completions` returned HTTP `200` and nonempty content for the required Chinese prompt
- Structured output: the existing `AgentResponse.model_json_schema()` request returned HTTP `200` and validated with Pydantic
- Client adaptation: `chat_template_kwargs.enable_thinking=false` is required so Qwen's final answer reaches `message.content` instead of consuming the response budget in `reasoning_content`
- Loaded GPU observation: RTX 5090 `32607 MiB` total, `19444 MiB` used, `12658 MiB` free
- Benchmark after one warmup, three non-streaming requests: mean total latency `0.187s`, minimum `0.182s`, mean `42.82` completion tokens/s; TTFT is not measured because the benchmark does not use streaming
- Cloud model metadata smoke: authenticated SSH tunnel health endpoints returned HTTP `200`; `home-5090-01` completed `ONLINE -> DEGRADED -> ONLINE` in the Cloud Registry without a Worker restart. The final Cloud record restored `Qwen/Qwen3.5-9B`, the pinned revision, and `local-dev` metadata.