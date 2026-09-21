import hashlib
import math
from typing import Protocol

import httpx


class EmbeddingProviderUnavailableError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ExternalOpenAIEmbeddingProvider:
    def __init__(self, *, base_url: str | None, api_key: str | None, model_id: str) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model_id = model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.base_url is None or self.api_key is None:
            raise EmbeddingProviderUnavailableError("Embedding provider is not configured")
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model_id, "input": texts},
                timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingProviderUnavailableError("Embedding request failed") from exc
        vectors = [item["embedding"] for item in response.json()["data"]]
        if any(len(vector) != 1536 for vector in vectors):
            raise EmbeddingProviderUnavailableError("Embedding provider returned an unexpected vector size")
        return vectors


class HashEmbeddingProvider:
    """Deterministic provider for tests and offline development only."""

    model_id = "hash-embedding-test"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * 1536
            for token in text.lower().split():
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % len(vector)
                vector[index] += 1.0
            magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / magnitude for value in vector])
        return vectors