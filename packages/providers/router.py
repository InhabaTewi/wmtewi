from dataclasses import dataclass

from pydantic import BaseModel

from packages.providers.base import LLMProvider, ProviderUnavailableError


@dataclass(frozen=True)
class RoutedProvider:
    provider: LLMProvider
    runtime_mode: str


class ProviderRouter:
    def __init__(self, external: LLMProvider, local: LLMProvider | None = None) -> None:
        self.external = external
        self.local = local

    async def select(self) -> RoutedProvider:
        if self.local is not None and await self.local.health():
            return RoutedProvider(provider=self.local, runtime_mode="local")
        if await self.external.health():
            return RoutedProvider(provider=self.external, runtime_mode="api")
        raise ProviderUnavailableError("No healthy LLM provider")

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> tuple[BaseModel, RoutedProvider]:
        route = await self.select()
        return await route.provider.generate(messages, response_schema, trace_id), route