import json

import httpx
import pytest

from apps.gpu_worker.local_model_client import (
    LocalModelAuthenticationError,
    LocalModelClient,
    LocalModelUnavailableError,
)


def client(handler) -> LocalModelClient:
    return LocalModelClient(
        base_url="http://127.0.0.1:18081/v1",
        api_key="local-secret",
        model_id="inaba-local-qwen",
        connect_timeout=1,
        read_timeout=1,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_health_requires_served_model_and_sends_bearer_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer local-secret"
        return httpx.Response(200, json={"data": [{"id": "inaba-local-qwen"}]})

    local_client = client(handler)
    assert await local_client.health() is True
    await local_client.aclose()


@pytest.mark.asyncio
async def test_health_reports_false_when_served_model_is_missing() -> None:
    local_client = client(lambda _request: httpx.Response(200, json={"data": [{"id": "other"}]}))
    assert await local_client.health() is False
    await local_client.aclose()


@pytest.mark.asyncio
async def test_health_maps_authentication_and_service_failures() -> None:
    authentication_client = client(lambda _request: httpx.Response(401))
    with pytest.raises(LocalModelAuthenticationError):
        await authentication_client.health()
    await authentication_client.aclose()

    unavailable_client = client(lambda _request: httpx.Response(503))
    with pytest.raises(LocalModelUnavailableError):
        await unavailable_client.health()
    await unavailable_client.aclose()


@pytest.mark.asyncio
async def test_generate_validates_agent_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"speech":"本地运行中"}'}}]})

    local_client = client(handler)
    response = await local_client.generate([{"role": "user", "content": "hello"}])
    assert response.speech == "本地运行中"
    await local_client.aclose()