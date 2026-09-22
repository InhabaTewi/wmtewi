from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


DEFAULT_EMBEDDING_DIMENSION = 1536
DEVELOPMENT_DATABASE_URL = "postgresql+psycopg://inaba:inaba@localhost:5432/inaba"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = DEVELOPMENT_DATABASE_URL
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_recycle: int = Field(default=1800, ge=0)
    database_connect_timeout: int = Field(default=10, gt=0)

    llm_provider: str = "openai-compatible"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_connect_timeout: float = Field(default=10.0, gt=0)
    llm_read_timeout: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0, le=2)

    embedding_provider: str = "openai-compatible"
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION
    embedding_connect_timeout: float = Field(default=10.0, gt=0)
    embedding_read_timeout: float = Field(default=30.0, gt=0)
    embedding_max_retries: int = Field(default=1, ge=0, le=2)

    service_token: SecretStr | None = None
    readiness_timeout: float = Field(default=5.0, gt=0, le=15)
    log_level: str = "INFO"
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    knowledge_data_path: Path = Path("data/knowledge")
    backup_path: Path = Path("data/backups")

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("embedding_dimension")
    @classmethod
    def validate_embedding_dimension(cls, value: int) -> int:
        if value != DEFAULT_EMBEDDING_DIMENSION:
            raise ValueError(
                f"EMBEDDING_DIMENSION must be {DEFAULT_EMBEDDING_DIMENSION} until the database schema changes"
            )
        return value

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.app_env != "production":
            return self

        required = {
            "LLM_BASE_URL": self.llm_base_url,
            "LLM_API_KEY": self.llm_api_key,
            "LLM_MODEL": self.llm_model,
            "EMBEDDING_BASE_URL": self.embedding_base_url,
            "EMBEDDING_API_KEY": self.embedding_api_key,
            "EMBEDDING_MODEL": self.embedding_model,
            "SERVICE_TOKEN": self.service_token,
        }
        missing = [name for name, value in required.items() if value is None or value == ""]
        if missing:
            raise ValueError(f"production configuration requires: {', '.join(missing)}")
        if self.database_url == DEVELOPMENT_DATABASE_URL or "inaba:inaba@" in self.database_url:
            raise ValueError("production DATABASE_URL must not use the development credentials")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("production ALLOWED_HOSTS must contain explicit hosts")
        for field_name, endpoint in {
            "LLM_BASE_URL": self.llm_base_url,
            "EMBEDDING_BASE_URL": self.embedding_base_url,
        }.items():
            if endpoint is not None and any(host in endpoint.lower() for host in ("localhost", "127.0.0.1")):
                raise ValueError(f"production {field_name} must not use a local development endpoint")
        for field_name, value in required.items():
            plain_value = value.get_secret_value() if isinstance(value, SecretStr) else value
            if isinstance(plain_value, str) and plain_value.lower() in {"change-me", "example", "placeholder"}:
                raise ValueError(f"production {field_name} must not use a placeholder value")
        return self

    @property
    def external_llm_base_url(self) -> str | None:
        """Compatibility alias for pre-M01 callers."""
        return self.llm_base_url

    @property
    def external_llm_api_key(self) -> SecretStr | None:
        """Compatibility alias for pre-M01 callers."""
        return self.llm_api_key

    @property
    def external_llm_model(self) -> str:
        """Compatibility alias for pre-M01 callers."""
        return self.llm_model


settings = Settings()
