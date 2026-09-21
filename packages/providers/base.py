from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel


class ProviderError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


@dataclass(frozen=True)
class ProviderHealth:
    state: Literal["healthy", "degraded", "unavailable"]
    detail: str

    @property
    def is_healthy(self) -> bool:
        return self.state in {"healthy", "degraded"}


class LLMProvider(Protocol):
    name: str
    model_id: str

    async def health(self) -> bool: ...

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel: ...

    async def aclose(self) -> None: ...