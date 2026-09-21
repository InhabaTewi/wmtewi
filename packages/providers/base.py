from typing import Protocol

from pydantic import BaseModel


class ProviderUnavailableError(RuntimeError):
    pass


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