from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.schemas.worker import WorkerIdentifier


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.worker", env_prefix="INABA_", extra="ignore")

    cloud_base_url: str
    service_token: SecretStr
    worker_id: WorkerIdentifier = "home-5090-01"
    worker_name: str = Field(default="Home RTX 5090", min_length=1, max_length=256)
    heartbeat_interval_seconds: int = Field(default=10, ge=1, le=300)
    gpu_probe: Literal["nvidia_smi"] = "nvidia_smi"
    gpu_index: int = Field(default=0, ge=0)
    http_connect_timeout: float = Field(default=5.0, gt=0, le=60)
    http_read_timeout: float = Field(default=15.0, gt=0, le=120)
    local_llm_enabled: bool = False
    local_llm_base_url: str = "http://127.0.0.1:18081/v1"
    local_llm_api_key: SecretStr | None = None
    local_llm_model: str = Field(default="inaba-local-qwen", min_length=1, max_length=256)
    local_llm_source_model: str = Field(default="Qwen/Qwen3.5-9B", min_length=3, max_length=256)
    local_llm_model_revision: str = Field(
        default="c202236235762e1c871ad0ccb60c8ee5ba337b9a",
        pattern=r"^[0-9a-f]{40}$",
    )

    @field_validator("cloud_base_url")
    @classmethod
    def validate_cloud_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("INABA_CLOUD_BASE_URL must be an absolute HTTP(S) URL without credentials")
        return value.rstrip("/")

    @field_validator("local_llm_base_url")
    @classmethod
    def validate_local_llm_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.path.rstrip("/") != "/v1"
        ):
            raise ValueError("INABA_LOCAL_LLM_BASE_URL must be a loopback HTTP /v1 URL without credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_enabled_local_llm(self) -> "WorkerSettings":
        if self.local_llm_enabled and self.local_llm_api_key is None:
            raise ValueError("INABA_LOCAL_LLM_API_KEY is required when INABA_LOCAL_LLM_ENABLED is true")
        return self

    @property
    def cloud_host(self) -> str:
        return urlsplit(self.cloud_base_url).netloc