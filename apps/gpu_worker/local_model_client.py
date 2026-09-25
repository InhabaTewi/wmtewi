import json

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from packages.schemas.chat import AgentResponse


class LocalModelError(RuntimeError):
    pass


class LocalModelUnavailableError(LocalModelError):
    pass


class LocalModelAuthenticationError(LocalModelError):
    pass


class LocalModelProtocolError(LocalModelError):
    pass


class LocalModelClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | str,
        model_id: str,
        connect_timeout: float,
        read_timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self.model_id = model_id
        self.timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._client = client or httpx.AsyncClient(
            timeout=self.timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self._owns_client = client is None

    async def health(self) -> bool:
        response = await self._request("GET", "/models")
        try:
            model_ids = {model["id"] for model in response.json()["data"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalModelProtocolError("Local model returned an invalid models response") from exc
        return self.model_id in model_ids

    async def generate(self, messages: list[dict[str, str]]) -> AgentResponse:
        response = await self._request(
            "POST",
            "/chat/completions",
            json={
                "model": self.model_id,
                "messages": messages,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": AgentResponse.__name__,
                        "schema": AgentResponse.model_json_schema(),
                    },
                },
            },
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return AgentResponse.model_validate(json.loads(content))
        except (IndexError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise LocalModelProtocolError("Local model returned invalid structured output") from exc

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.TimeoutException as exc:
            raise LocalModelUnavailableError("Local model request timed out") from exc
        except httpx.RequestError as exc:
            raise LocalModelUnavailableError("Local model request failed") from exc
        if response.status_code in {401, 403}:
            raise LocalModelAuthenticationError("Local model authentication failed")
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise LocalModelUnavailableError("Local model is unavailable")
        if not response.is_success:
            raise LocalModelProtocolError(f"Local model returned HTTP {response.status_code}")
        return response

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()