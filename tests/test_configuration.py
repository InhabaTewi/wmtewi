from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.control_api.dependencies import (
    create_database_engine,
    database_engine_options,
    get_knowledge_service,
    get_provider_router,
)
from packages.persistence.config import DEFAULT_EMBEDDING_DIMENSION, Settings


def test_development_defaults_preserve_local_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.embedding_dimension == DEFAULT_EMBEDDING_DIMENSION
    assert settings.knowledge_data_path == Path("data/knowledge")


def test_test_environment_supports_sqlite_without_provider_credentials() -> None:
    settings = Settings(app_env="test", database_url="sqlite://", _env_file=None)

    assert settings.database_url == "sqlite://"
    assert settings.llm_api_key is None
    assert settings.embedding_api_key is None


def test_production_requires_configuration_without_leaking_secrets() -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL"):
        Settings(app_env="production", _env_file=None)


def test_production_rejects_development_provider_endpoint() -> None:
    with pytest.raises(ValidationError, match="local development endpoint"):
        Settings(
            app_env="production",
            database_url="postgresql+psycopg://inaba:secure-password@db.example/inaba",
            llm_base_url="http://localhost:8000/v1",
            llm_api_key="llm-secret",
            embedding_base_url="https://embedding.example/v1",
            embedding_api_key="embedding-secret",
            service_token="service-secret",
            allowed_hosts=["api.example"],
            _env_file=None,
        )


def test_llm_and_embedding_factories_use_independent_credentials(monkeypatch, session) -> None:
    configured = Settings(
        app_env="test",
        llm_base_url="https://llm.example/v1",
        llm_api_key="llm-secret",
        llm_model="llm-model",
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key="embedding-secret",
        embedding_model="embedding-model",
        _env_file=None,
    )
    monkeypatch.setattr("apps.control_api.dependencies.settings", configured)

    router = get_provider_router()
    knowledge = get_knowledge_service(session)

    assert router.external.base_url == "https://llm.example/v1"
    assert router.external.api_key == "llm-secret"
    assert knowledge.embedding_provider.base_url == "https://embedding.example/v1"
    assert knowledge.embedding_provider.api_key == "embedding-secret"


def test_settings_repr_redacts_secrets() -> None:
    settings = Settings(
        llm_api_key="llm-secret",
        embedding_api_key="embedding-secret",
        service_token="service-secret",
        _env_file=None,
    )

    rendered = repr(settings)

    assert "llm-secret" not in rendered
    assert "embedding-secret" not in rendered
    assert "service-secret" not in rendered


def test_embedding_dimension_and_paths_parse_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1536")
    monkeypatch.setenv("KNOWLEDGE_DATA_PATH", "/srv/inaba/knowledge")
    monkeypatch.setenv("BACKUP_PATH", "/srv/inaba/backups")
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example,admin.example")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example,https://admin.example")

    settings = Settings(_env_file=None)

    assert settings.embedding_dimension == 1536
    assert settings.knowledge_data_path == Path("/srv/inaba/knowledge")
    assert settings.backup_path == Path("/srv/inaba/backups")
    assert settings.allowed_hosts == ["api.example", "admin.example"]
    assert settings.cors_origins == ["https://app.example", "https://admin.example"]


def test_database_engine_uses_postgres_pool_settings(monkeypatch) -> None:
    configured = Settings(
        database_url="postgresql+psycopg://user:password@db.example/inaba",
        database_pool_size=7,
        database_max_overflow=3,
        database_pool_recycle=120,
        database_connect_timeout=9,
        _env_file=None,
    )
    monkeypatch.setattr("apps.control_api.dependencies.settings", configured)

    options = database_engine_options(configured.database_url)

    assert options["pool_pre_ping"] is True
    assert options["pool_size"] == 7
    assert options["max_overflow"] == 3
    assert options["pool_recycle"] == 120
    assert options["connect_args"] == {"connect_timeout": 9}


def test_database_engine_keeps_sqlite_free_of_postgres_pool_options() -> None:
    assert database_engine_options("sqlite://") == {}
    engine = create_database_engine("sqlite://")
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()