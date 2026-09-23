import json

import httpx
import pytest

from packages.knowledge.embedding import ExternalOpenAIEmbeddingProvider
from packages.providers.base import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def vector() -> list[float]:
    return [0.0] * 1536


@pytest.mark.asyncio
async def test_embedding_provider_uses_independent_endpoint_key_and_validates_vectors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://embedding.example/v1/embeddings"
        assert request.headers["authorization"] == "Bearer embedding-secret"
        return httpx.Response(200, json={"data": [{"embedding": vector()}]})

    provider = ExternalOpenAIEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        model_id="embedding-model",
        transport=httpx.MockTransport(handler),
    )

    assert len((await provider.aembed(["text"]))[0]) == 1536
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(401, ProviderAuthenticationError), (429, ProviderRateLimitError), (500, ProviderUnavailableError), (504, ProviderTimeoutError)],
)
async def test_embedding_provider_maps_http_errors(status_code, error_type) -> None:
    provider = ExternalOpenAIEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        model_id="embedding-model",
        max_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code)),
    )

    with pytest.raises(error_type):
        await provider.aembed(["text"])
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{"embedding": [0.0]}]},
        {"data": [{"embedding": [float("nan")] * 1536}]},
    ],
)
async def test_embedding_provider_rejects_invalid_response(payload) -> None:
    provider = ExternalOpenAIEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        model_id="embedding-model",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=(
                        '{"data":[{"embedding":[' + ",".join(["NaN"] * 1536) + "]}]}"
                        if payload == {"data": [{"embedding": [float("nan")] * 1536}]}
                        else json.dumps(payload)
                    ),
                    headers={"content-type": "application/json"},
                )
            ),
    )

    with pytest.raises(ProviderResponseError):
        await provider.aembed(["text"])
    await provider.aclose()


@pytest.mark.asyncio
async def test_embedding_provider_retries_and_health_fallback() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if request.url.path.endswith("/models"):
            assert request.headers["authorization"] == "Bearer embedding-secret"
            return httpx.Response(404)
        assert json.loads(request.content)["dimensions"] == 1536
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"embedding": vector()}]})

    provider = ExternalOpenAIEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="embedding-secret",
        model_id="embedding-model",
        max_retries=1,
        retry_base_delay=0,
        transport=httpx.MockTransport(handler),
    )

    assert (await provider.health_status()).state == "degraded"
    assert len(await provider.aembed(["text"])) == 1
    await provider.aclose()