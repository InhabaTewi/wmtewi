import json

import httpx
from pydantic import BaseModel

from packages.providers.base import ProviderUnavailableError


class ExternalOpenAIProvider:
    name = "external-openai"

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model_id = model_id
        self.transport = transport

    async def health(self) -> bool:
        return self.base_url is not None and self.api_key is not None

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        if not await self.health():
            raise ProviderUnavailableError("External LLM provider is not configured")
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Trace-ID": trace_id}
        payload = {
            "model": self.model_id,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "schema": response_schema.model_json_schema(),
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("External LLM request failed") from exc
        content = response.json()["choices"][0]["message"]["content"]
        return response_schema.model_validate(json.loads(content))