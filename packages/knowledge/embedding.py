import hashlib
import math
import random
from asyncio import sleep
from typing import Protocol

import httpx

from pydantic import SecretStr

from packages.persistence.config import DEFAULT_EMBEDDING_DIMENSION
from packages.providers.base import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderHealth,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


EmbeddingProviderUnavailableError = ProviderUnavailableError


class EmbeddingProvider(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def aembed(self, texts: list[str]) -> list[list[float]]: ...


class ExternalOpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: SecretStr | str | None,
        model_id: str,
        dimension: int = DEFAULT_EMBEDDING_DIMENSION,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        retry_base_delay: float = 0.1,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self.model_id = model_id
        self.dimension = dimension
        self.timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
        self.max_retries = max_retries
        self.transport = transport
        self.retry_base_delay = retry_base_delay
        self._client = client
        self._owns_client = client is None

    def _require_configuration(self) -> None:
        if self.base_url is None or self.api_key is None:
            raise ProviderConfigurationError("Embedding provider is not configured")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, transport=self.transport)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @staticmethod
    def _error_for_status(status_code: int):
        if status_code in {401, 403}:
            return ProviderAuthenticationError("Embedding provider authentication failed")
        if status_code == 429:
            return ProviderRateLimitError("Embedding provider rate limit exceeded")
        if status_code in {408, 504}:
            return ProviderTimeoutError("Embedding provider request timed out")
        if status_code in {500, 502, 503}:
            return ProviderUnavailableError("Embedding provider is unavailable")
        return ProviderResponseError(f"Embedding provider returned HTTP {status_code}")

    async def _request(
        self, method: str, path: str, *, allowed_statuses: set[int] | None = None, **kwargs
    ) -> httpx.Response:
        self._require_configuration()
        retries = 0
        while True:
            try:
                response = await self._get_client().request(method, f"{self.base_url}{path}", **kwargs)
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError("Embedding provider request timed out") from exc
            except httpx.RequestError:
                error = ProviderUnavailableError("Embedding provider network request failed")
                retryable = True
            else:
                if response.is_success or response.status_code in (allowed_statuses or set()):
                    return response
                error = self._error_for_status(response.status_code)
                retryable = response.status_code in {429, 502, 503, 504}
            if not retryable or retries >= self.max_retries:
                raise error
            await sleep(self.retry_base_delay * (2**retries) + random.uniform(0, self.retry_base_delay))
            retries += 1

    async def health(self) -> bool:
        return (await self.health_status()).is_healthy

    async def health_status(self) -> ProviderHealth:
        try:
            response = await self._request(
                "GET",
                "/models",
                allowed_statuses={404},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except ProviderConfigurationError:
            return ProviderHealth("unavailable", "not configured")
        except ProviderAuthenticationError:
            return ProviderHealth("unavailable", "authentication failed")
        except ProviderResponseError:
            return ProviderHealth("unavailable", "health response invalid")
        except ProviderUnavailableError:
            return ProviderHealth("unavailable", "endpoint unavailable")
        if response.status_code == 404:
            return ProviderHealth("degraded", "lightweight health endpoint unsupported")
        return ProviderHealth("healthy", "models endpoint reachable")

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        self._require_configuration()
        if not texts:
            raise ProviderResponseError("Embedding input must not be empty")
        response = await self._request(
            "POST",
            "/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model_id, "input": texts, "dimensions": self.dimension},
        )
        try:
            data = response.json()["data"]
            vectors = [item["embedding"] for item in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError("Embedding provider returned malformed response") from exc
        if len(vectors) != len(texts) or any(
            not isinstance(vector, list)
            or len(vector) != self.dimension
            or not vector
            or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise ProviderResponseError("Embedding provider returned invalid vectors")
        return [[float(value) for value in vector] for vector in vectors]

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("External embedding is async; use aembed()")


class HashEmbeddingProvider:
    """Deterministic provider for tests and offline development only."""

    model_id = "hash-embedding-test"

    def __init__(self, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in text.lower().split():
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % len(vector)
                vector[index] += 1.0
            magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / magnitude for value in vector])
        return vectors

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return self.embed(texts)