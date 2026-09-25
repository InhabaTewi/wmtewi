import pytest
from pydantic import ValidationError

from apps.gpu_worker.config import WorkerSettings


def configured_settings(**overrides) -> WorkerSettings:
    values = {
        "cloud_base_url": "http://127.0.0.1:18151/tewi/",
        "service_token": "secret-token",
    }
    values.update(overrides)
    return WorkerSettings(_env_file=None, **values)


def test_worker_settings_accept_valid_configuration_and_normalize_url() -> None:
    settings = configured_settings()

    assert settings.cloud_base_url == "http://127.0.0.1:18151/tewi"
    assert settings.heartbeat_interval_seconds == 10
    assert settings.cloud_host == "127.0.0.1:18151"


@pytest.mark.parametrize("url", ["cloud.example", "ftp://cloud.example", "https://user:pass@cloud.example"])
def test_worker_settings_reject_invalid_cloud_urls(url: str) -> None:
    with pytest.raises(ValidationError, match="INABA_CLOUD_BASE_URL"):
        configured_settings(cloud_base_url=url)


def test_worker_settings_require_service_token_and_bound_heartbeat_interval(monkeypatch) -> None:
    monkeypatch.delenv("INABA_SERVICE_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        WorkerSettings(cloud_base_url="https://cloud.example", _env_file=None)
    with pytest.raises(ValidationError):
        configured_settings(heartbeat_interval_seconds=0)


def test_worker_settings_redact_service_token() -> None:
    assert "secret-token" not in repr(configured_settings())


def test_worker_settings_restrict_enabled_local_model_to_loopback_and_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("INABA_LOCAL_LLM_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="INABA_LOCAL_LLM_API_KEY"):
        configured_settings(local_llm_enabled=True)
    with pytest.raises(ValidationError, match="INABA_LOCAL_LLM_BASE_URL"):
        configured_settings(
            local_llm_enabled=True,
            local_llm_api_key="local-secret",
            local_llm_base_url="http://0.0.0.0:18081/v1",
        )

    configured = configured_settings(local_llm_enabled=True, local_llm_api_key="local-secret")
    assert "local-secret" not in repr(configured)
    assert configured.local_llm_source_model == "Qwen/Qwen3.5-9B"