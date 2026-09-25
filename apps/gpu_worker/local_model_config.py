from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class LocalModelRuntimeConfig(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    hf_repo: str = Field(min_length=3, max_length=256)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    served_model_name: str = Field(min_length=1, max_length=256)
    engine: str = "vllm"
    vllm_image: str = Field(pattern=r"^vllm/vllm-openai@sha256:[0-9a-f]{64}$")
    host: str = "127.0.0.1"
    port: int = Field(ge=1024, le=65535)
    max_model_len: int = Field(gt=0)
    max_num_seqs: int = Field(gt=0)
    gpu_memory_utilization: float = Field(ge=0.75, le=0.85)
    dtype: str = "auto"
    enforce_eager: bool = True

    @field_validator("host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        if value != "127.0.0.1":
            raise ValueError("Local vLLM must bind to 127.0.0.1")
        return value

    @model_validator(mode="after")
    def validate_vllm_engine(self) -> "LocalModelRuntimeConfig":
        if self.engine != "vllm":
            raise ValueError("Local runtime engine must be vllm")
        return self


class LlamaCppRuntimeConfig(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    source_hf_repo: str = Field(min_length=3, max_length=256)
    source_hf_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    gguf_path: str = Field(min_length=1)
    gguf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine: str = "llama_cpp"
    llama_cpp_version: str = Field(min_length=1, max_length=128)
    llama_cpp_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    llama_cpp_artifact: str = Field(min_length=1)
    llama_cpp_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    llama_cpp_cudart_artifact: str = Field(min_length=1)
    llama_cpp_cudart_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    llama_server_path: str = Field(min_length=1)
    state_file: str = Field(min_length=1)
    api_key_file: str = Field(min_length=1)
    served_model_name: str = Field(min_length=1, max_length=256)
    host: str = "127.0.0.1"
    port: int = Field(ge=1024, le=65535)
    context_size: int = Field(gt=0)
    gpu_layers: str = "all"

    @field_validator("host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        if value != "127.0.0.1":
            raise ValueError("Local llama.cpp must bind to 127.0.0.1")
        return value

    @model_validator(mode="after")
    def validate_llama_cpp_engine(self) -> "LlamaCppRuntimeConfig":
        if self.engine != "llama_cpp":
            raise ValueError("Local runtime engine must be llama_cpp")
        if self.gpu_layers != "all":
            raise ValueError("Local llama.cpp baseline must offload all GPU layers")
        return self


def load_local_model_runtime_config(path: Path) -> LocalModelRuntimeConfig | LlamaCppRuntimeConfig:
    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read local model configuration: {path}") from exc
    if not isinstance(raw_config, dict):
        raise ValueError("Local model configuration must be a YAML mapping")
    if raw_config.get("engine") == "llama_cpp":
        return LlamaCppRuntimeConfig.model_validate(raw_config)
    return LocalModelRuntimeConfig.model_validate(raw_config)