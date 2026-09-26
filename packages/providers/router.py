from dataclasses import dataclass
import logging

from pydantic import BaseModel

from packages.providers.base import LLMProvider, ProviderFailure, ProviderUnavailableError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutedProvider:
    provider: LLMProvider
    runtime_mode: str


class ProviderRouter:
    def __init__(self, external: LLMProvider | None, local: LLMProvider | None = None, mode: str = "auto") -> None:
        self.external = external
        self.local = local
        self.mode = mode

    async def select(self) -> RoutedProvider:
        if self.mode == "cloud":
            if self.external is not None and await self.external.health():
                return RoutedProvider(provider=self.external, runtime_mode="api")
            raise ProviderUnavailableError("Cloud LLM provider is unavailable")
        if self.mode == "local_worker":
            if self.local is not None and await self.local.health():
                return RoutedProvider(provider=self.local, runtime_mode="local")
            availability_failure = getattr(self.local, "availability_failure", None) if self.local is not None else None
            if availability_failure is not None:
                raise await availability_failure()
            raise ProviderUnavailableError("Local Worker provider is unavailable")
        if self.local is not None and await self.local.health():
            return RoutedProvider(provider=self.local, runtime_mode="local")
        if self.external is not None and await self.external.health():
            return RoutedProvider(provider=self.external, runtime_mode="api")
        raise ProviderUnavailableError("No healthy LLM provider")

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> tuple[BaseModel, RoutedProvider]:
        route = await self.select()
        try:
            return await route.provider.generate(messages, response_schema, trace_id), route
        except ProviderFailure as exc:
            logger.warning(
                "Provider request failed provider=%s failure_kind=%s fallback_eligible=%s trace_id=%s",
                exc.provider,
                exc.kind.value,
                exc.fallback_eligible,
                trace_id,
            )
            raise