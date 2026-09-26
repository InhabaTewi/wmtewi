from dataclasses import dataclass

from packages.providers.base import ProviderFailure


@dataclass(frozen=True)
class FailoverDecision:
    fallback_allowed: bool
    reason: str
    failure_kind: str


class FailoverPolicy:
    """Classifies a failure only; T08-5 does not execute a fallback."""

    def decide(self, failure: ProviderFailure) -> FailoverDecision:
        return FailoverDecision(
            fallback_allowed=failure.fallback_eligible,
            reason=("eligible transient local-provider failure" if failure.fallback_eligible else "fallback is unsafe for this failure"),
            failure_kind=failure.kind.value,
        )