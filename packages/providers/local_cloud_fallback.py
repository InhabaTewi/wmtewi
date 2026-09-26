from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel

from packages.providers.base import LLMProvider, ProviderError, ProviderFailure, ProviderHealth
from packages.providers.failover import FailoverPolicy


@dataclass(frozen=True)
class FailoverExecution:
    response: BaseModel
    final_provider: LLMProvider
    runtime_mode: str
    trace_metadata: dict[str, object]


class PreferLocalWithCloudFallbackProvider:
    """Uses Cloud only after an eligible, typed Local Worker failure."""

    name = "prefer-local-with-cloud-fallback"
    model_id = "local-first"

    def __init__(self, local: LLMProvider, cloud: LLMProvider, policy: FailoverPolicy) -> None:
        self.local = local
        self.cloud = cloud
        self.policy = policy

    async def health(self) -> bool:
        return (await self.health_status()).is_healthy

    async def health_status(self) -> ProviderHealth:
        local_healthy = await self.local.health()
        cloud_healthy = await self.cloud.health()
        if local_healthy and cloud_healthy:
            return ProviderHealth("healthy", "local and cloud providers available")
        if local_healthy or cloud_healthy:
            return ProviderHealth("degraded", "one failover provider available")
        return ProviderHealth("unavailable", "local and cloud providers unavailable")

    async def generate(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        return (await self.generate_execution(messages, response_schema, trace_id)).response

    async def generate_execution(
        self,
        messages: list[dict[str, str]],
        response_schema: type[BaseModel],
        trace_id: str,
    ) -> FailoverExecution:
        started = perf_counter()
        try:
            response = await self.local.generate(messages, response_schema, trace_id)
        except ProviderFailure as failure:
            primary_duration_ms = round((perf_counter() - started) * 1000)
            decision = self.policy.decide(failure)
            if not decision.fallback_allowed:
                raise
            fallback_started = perf_counter()
            try:
                response = await self.cloud.generate(messages, response_schema, trace_id)
            except ProviderError as exc:
                exc.failover_trace_metadata = {
                    "provider_path": "local_to_cloud_fallback",
                    "primary_provider": self.local.name,
                    "primary_failure_kind": failure.kind.value,
                    "primary_duration_ms": primary_duration_ms,
                    "fallback_attempted": True,
                    "fallback_provider": self.cloud.name,
                    "fallback_duration_ms": round((perf_counter() - fallback_started) * 1000),
                    "fallback_failure_type": type(exc).__name__,
                    "final_provider": self.cloud.name,
                }
                raise
            return FailoverExecution(
                response=response,
                final_provider=self.cloud,
                runtime_mode="api",
                trace_metadata={
                    "provider_path": "local_to_cloud_fallback",
                    "primary_provider": self.local.name,
                    "primary_failure_kind": failure.kind.value,
                    "primary_duration_ms": primary_duration_ms,
                    "fallback_attempted": True,
                    "fallback_provider": self.cloud.name,
                    "fallback_duration_ms": round((perf_counter() - fallback_started) * 1000),
                    "final_provider": self.cloud.name,
                },
            )
        return FailoverExecution(
            response=response,
            final_provider=self.local,
            runtime_mode="local",
            trace_metadata={
                "provider_path": "local",
                "primary_provider": self.local.name,
                "primary_duration_ms": round((perf_counter() - started) * 1000),
                "fallback_attempted": False,
                "final_provider": self.local.name,
            },
        )

    async def aclose(self) -> None:
        return None