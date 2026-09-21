from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://inaba:inaba@localhost:5432/inaba"
    external_llm_base_url: str | None = None
    external_llm_api_key: str | None = None
    external_llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"


settings = Settings()
