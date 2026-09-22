import json

import httpx
import pytest

from packages.providers.external_openai import ExternalOpenAIProvider
from packages.providers.base import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from packages.schemas.chat import AgentResponse


@pytest.mark.asyncio
async def test_external_provider_completes_openai_compatible_chat() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["x-trace-id"] == "trace-1"
        assert json.loads(request.content)["model"] == "test-model"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"speech":"pong"}'}}]})

    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        transport=httpx.MockTransport(handler),
    )

    response = await provider.generate([{"role": "user", "content": "ping"}], AgentResponse, "trace-1")

    assert response.speech == "pong"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, ProviderAuthenticationError),
        (403, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (502, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (504, ProviderTimeoutError),
    ],
)
async def test_external_provider_maps_http_errors(status_code, error_type) -> None:
    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        max_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code)),
    )

    with pytest.raises(error_type) as raised:
        await provider.generate([], AgentResponse, "trace")

    assert "secret" not in str(raised.value)
    await provider.aclose()


@pytest.mark.asyncio
async def test_external_provider_retries_only_retryable_errors() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"speech":"ok"}'}}]})

    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        max_retries=1,
        retry_base_delay=0,
        transport=httpx.MockTransport(handler),
    )

    assert (await provider.generate([], AgentResponse, "trace")).speech == "ok"
    assert attempts == 2
    await provider.aclose()


@pytest.mark.asyncio
async def test_external_provider_rejects_invalid_structured_output_and_closes_client() -> None:
    provider = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []})),
    )

    with pytest.raises(ProviderResponseError):
        await provider.generate([], AgentResponse, "trace")

    client = provider._client
    await provider.aclose()
    assert client is not None and client.is_closed


@pytest.mark.asyncio
async def test_external_provider_health_supports_models_fallback_and_auth_failure() -> None:
    def models_not_found(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(404)

    degraded = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        transport=httpx.MockTransport(models_not_found),
    )
    unauthenticated = ExternalOpenAIProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model_id="test-model",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    assert (await degraded.health_status()).state == "degraded"
    assert (await unauthenticated.health_status()).state == "unavailable"
    await degraded.aclose()
    await unauthenticated.aclose()