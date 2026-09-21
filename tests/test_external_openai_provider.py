import json

import httpx
import pytest

from packages.providers.external_openai import ExternalOpenAIProvider
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