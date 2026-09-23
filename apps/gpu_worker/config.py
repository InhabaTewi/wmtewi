from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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

    @field_validator("cloud_base_url")
    @classmethod
    def validate_cloud_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("INABA_CLOUD_BASE_URL must be an absolute HTTP(S) URL without credentials")
        return value.rstrip("/")

    @property
    def cloud_host(self) -> str:
        return urlsplit(self.cloud_base_url).netloc