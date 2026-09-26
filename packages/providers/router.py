from dataclasses import dataclass
import logging
from typing import Literal

from pydantic import BaseModel

from packages.providers.base import LLMProvider, ProviderFailure, ProviderUnavailableError
from packages.providers.failover import FailoverPolicy
from packages.providers.local_cloud_fallback import PreferLocalWithCloudFallbackProvider


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutedProvider:
    provider: LLMProvider
    runtime_mode: str
    trace_metadata: dict[str, object] | None = None


class ProviderRouter:
    def __init__(
        self,
        external: LLMProvider | None,
        local: LLMProvider | None = None,
        mode: Literal["cloud", "local_worker", "prefer_local_with_cloud_fallback"] = "cloud",
    ) -> None:
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
        if self.mode == "prefer_local_with_cloud_fallback":
            if self.local is not None and self.external is not None:
                provider = PreferLocalWithCloudFallbackProvider(self.local, self.external, FailoverPolicy())
                if await provider.health():
                    return RoutedProvider(provider=provider, runtime_mode="local")
            raise ProviderUnavailableError("Local and Cloud LLM providers are unavailable")
        raise ValueError(f"Unsupported LLM provider mode: {self.mode}")

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> tuple[BaseModel, RoutedProvider]:
        route = await self.select()
        if isinstance(route.provider, PreferLocalWithCloudFallbackProvider):
            execution = await route.provider.generate_execution(messages, response_schema, trace_id)
            return execution.response, RoutedProvider(
                provider=execution.final_provider,
                runtime_mode=execution.runtime_mode,
                trace_metadata=execution.trace_metadata,
            )
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