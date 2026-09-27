import json
import random
from asyncio import sleep
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from packages.providers.base import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@dataclass(frozen=True)
class HealthFailureDiagnostic:
    failure_stage: str
    failure_kind: str
    failure_reason_code: str
    exception_class: str
    http_status: int | None


class ExternalOpenAIProvider:
    name = "external-openai"

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: SecretStr | str | None,
        model_id: str,
        connect_timeout: float = 10.0,
        read_timeout: float = 60.0,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        retry_base_delay: float = 0.1,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self.model_id = model_id
        self.timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
        self.transport = transport
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self._client = client
        self._owns_client = client is None
        self.last_health_failure: HealthFailureDiagnostic | None = None

    def _require_configuration(self) -> None:
        if self.base_url is None or self.api_key is None:
            raise ProviderConfigurationError("External LLM provider is not configured")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, transport=self.transport)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @staticmethod
    def _error_for_status(status_code: int) -> ProviderUnavailableError:
        if status_code in {401, 403}:
            return ProviderAuthenticationError("External LLM provider authentication failed")
        if status_code == 429:
            return ProviderRateLimitError("External LLM provider rate limit exceeded")
        if status_code in {408, 504}:
            return ProviderTimeoutError("External LLM provider request timed out")
        if status_code in {500, 502, 503}:
            return ProviderUnavailableError("External LLM provider is unavailable")
        return ProviderResponseError(f"External LLM provider returned HTTP {status_code}")

    async def _request(
        self, method: str, path: str, *, allowed_statuses: set[int] | None = None, **kwargs
    ) -> httpx.Response:
        self._require_configuration()
        retries = 0
        while True:
            try:
                response = await self._get_client().request(method, f"{self.base_url}{path}", **kwargs)
            except httpx.TimeoutException as exc:
                error = ProviderTimeoutError("External LLM provider request timed out")
                error.health_exception_class = type(exc).__name__
                raise error from exc
            except httpx.RequestError as exc:
                error = ProviderUnavailableError("External LLM provider network request failed")
                error.health_exception_class = type(exc).__name__
                retryable = True
            else:
                if response.is_success or response.status_code in (allowed_statuses or set()):
                    return response
                error = self._error_for_status(response.status_code)
                error.health_http_status = response.status_code
                retryable = response.status_code in {429, 502, 503, 504}
            if not retryable or retries >= self.max_retries:
                raise error
            await sleep(self.retry_base_delay * (2**retries) + random.uniform(0, self.retry_base_delay))
            retries += 1

    async def health(self) -> bool:
        return (await self.health_status()).is_healthy

    def _record_health_failure(self, exc: Exception) -> None:
        http_status = getattr(exc, "health_http_status", None)
        exception_class = getattr(exc, "health_exception_class", type(exc).__name__)
        if http_status in {401, 403}:
            kind, reason = "authentication", "provider_authentication_failed"
        elif http_status == 429:
            kind, reason = "rate_limit", "provider_rate_limited"
        elif http_status in {408, 504}:
            kind, reason = "timeout", "provider_request_timeout"
        elif http_status in {500, 502, 503}:
            kind, reason = "unavailable", "provider_server_error"
        elif http_status is not None:
            kind, reason = "response", "provider_unexpected_response"
        elif isinstance(exc, ProviderConfigurationError):
            kind, reason = "configuration", "provider_not_configured"
        elif isinstance(exc, ProviderAuthenticationError):
            kind, reason = "authentication", "provider_authentication_failed"
        elif isinstance(exc, ProviderRateLimitError):
            kind, reason = "rate_limit", "provider_rate_limited"
        elif isinstance(exc, ProviderTimeoutError):
            kind, reason = "timeout", "provider_request_timeout"
        elif isinstance(exc, ProviderUnavailableError):
            kind, reason = "connection", "provider_network_error"
        elif isinstance(exc, ProviderResponseError):
            kind, reason = "response", "provider_unexpected_response"
        else:
            kind, reason = "unknown", "provider_exception"
        self.last_health_failure = HealthFailureDiagnostic(
            failure_stage="classify_response" if http_status is not None else "request_models",
            failure_kind=kind,
            failure_reason_code=reason,
            exception_class=exception_class,
            http_status=http_status,
        )

    async def health_status(self) -> ProviderHealth:
        self.last_health_failure = None
        try:
            response = await self._request(
                "GET",
                "/models",
                allowed_statuses={404},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except ProviderConfigurationError as exc:
            self._record_health_failure(exc)
            return ProviderHealth("unavailable", "not configured")
        except ProviderAuthenticationError as exc:
            self._record_health_failure(exc)
            return ProviderHealth("unavailable", "authentication failed")
        except ProviderUnavailableError as exc:
            self._record_health_failure(exc)
            return ProviderHealth("unavailable", "endpoint unavailable")
        except Exception as exc:
            self._record_health_failure(exc)
            raise
        if response.status_code == 404:
            return ProviderHealth("degraded", "lightweight health endpoint unsupported")
        return ProviderHealth("healthy", "models endpoint reachable")

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        self._require_configuration()
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
        response = await self._request("POST", "/chat/completions", headers=headers, json=payload)
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return response_schema.model_validate(json.loads(content))
        except (IndexError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderResponseError("External LLM provider returned invalid structured output") from exc