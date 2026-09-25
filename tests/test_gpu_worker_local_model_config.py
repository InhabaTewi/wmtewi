from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.gpu_worker.local_model_config import (
    LlamaCppRuntimeConfig,
    LocalModelRuntimeConfig,
    load_local_model_runtime_config,
)


VALID_CONFIG = {
    "model_id": "qwen3.5-9b",
    "hf_repo": "Qwen/Qwen3.5-9B",
    "revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
    "served_model_name": "inaba-local-qwen",
    "engine": "vllm",
    "vllm_image": "vllm/vllm-openai@sha256:" + "a" * 64,
    "host": "127.0.0.1",
    "port": 18081,
    "max_model_len": 8192,
    "max_num_seqs": 2,
    "gpu_memory_utilization": 0.78,
    "dtype": "auto",
}


def test_local_runtime_config_requires_immutable_image_and_loopback() -> None:
    runtime_config = LocalModelRuntimeConfig.model_validate(VALID_CONFIG)
    assert runtime_config.vllm_image.endswith("a" * 64)

    with pytest.raises(ValidationError):
        LocalModelRuntimeConfig.model_validate({**VALID_CONFIG, "vllm_image": "vllm/vllm-openai:latest"})
    with pytest.raises(ValidationError, match="127.0.0.1"):
        LocalModelRuntimeConfig.model_validate({**VALID_CONFIG, "host": "0.0.0.0"})


def test_load_local_runtime_config_rejects_non_mapping_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "local-model.yaml"
    config_path.write_text("- not\n- a mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML mapping"):
        load_local_model_runtime_config(config_path)


def test_llama_cpp_runtime_config_requires_pinned_artifacts_and_loopback() -> None:
    config = LlamaCppRuntimeConfig.model_validate(
        {
            "model_id": "qwen3.5-9b-f16",
            "source_hf_repo": "Qwen/Qwen3.5-9B",
            "source_hf_revision": "c202236235762e1c871ad0ccb60c8ee5ba337b9a",
            "gguf_path": "D:/AIGC/models/Qwen3.5-9B/gguf/revision/Qwen3.5-9B-F16.gguf",
            "gguf_sha256": "a" * 64,
            "engine": "llama_cpp",
            "llama_cpp_version": "0.5.0-dev (build 11146)",
            "llama_cpp_commit": "7fe450e19305b828c199d602c23a8337aaa1f03b",
            "llama_cpp_artifact": "llama-b11146-bin-win-cuda-13.4-x64.zip",
            "llama_cpp_artifact_sha256": "b" * 64,
            "llama_cpp_cudart_artifact": "cudart-llama-bin-win-cuda-13.4-x64.zip",
            "llama_cpp_cudart_artifact_sha256": "c" * 64,
            "llama_server_path": "D:/AIGC/runtimes/llama.cpp/b11146/llama-server.exe",
            "state_file": "D:/AIGC/runtimes/llama.cpp/b11146/inaba-local-llamacpp.state.json",
            "api_key_file": "D:/AIGC/runtimes/llama.cpp/b11146/inaba-local-llamacpp.api-key",
            "served_model_name": "inaba-local-qwen",
            "host": "127.0.0.1",
            "port": 18081,
            "context_size": 8192,
            "gpu_layers": "all",
        }
    )
    assert config.engine == "llama_cpp"

    with pytest.raises(ValidationError, match="127.0.0.1"):
        LlamaCppRuntimeConfig.model_validate({**config.model_dump(), "host": "0.0.0.0"})
    with pytest.raises(ValidationError, match="all GPU layers"):
        LlamaCppRuntimeConfig.model_validate({**config.model_dump(), "gpu_layers": "32"})


def test_committed_qwen_runtime_manifest_is_immutable_and_loopback_only() -> None:
    path = Path("configs/local_models/qwen3.5-9b.yaml")
    runtime_config = load_local_model_runtime_config(path)

    assert runtime_config.hf_repo == "Qwen/Qwen3.5-9B"
    assert runtime_config.host == "127.0.0.1"
    assert "@sha256:" in runtime_config.vllm_image
    assert runtime_config.enforce_eager is True


def test_committed_llama_cpp_runtime_manifest_is_pinned_and_loopback_only() -> None:
    path = Path("configs/local_models/qwen3.5-9b-llamacpp.yaml")
    runtime_config = load_local_model_runtime_config(path)

    assert runtime_config.source_hf_repo == "Qwen/Qwen3.5-9B"
    assert runtime_config.source_hf_revision == "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
    assert runtime_config.gguf_sha256 == "88fbfd78c7e5e74b01ad7868e93f7779feff3101b02dabc5e33e23a7fa2b78ff"
    assert runtime_config.host == "127.0.0.1"
    assert runtime_config.gpu_layers == "all"