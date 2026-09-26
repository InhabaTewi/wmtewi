from dataclasses import dataclass
from enum import Enum
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


class ProviderFailureKind(str, Enum):
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    JOB_QUEUE_TIMEOUT = "JOB_QUEUE_TIMEOUT"
    JOB_LEASE_EXPIRED = "JOB_LEASE_EXPIRED"
    LOCAL_INFERENCE_TIMEOUT = "LOCAL_INFERENCE_TIMEOUT"
    LOCAL_INFERENCE_ERROR = "LOCAL_INFERENCE_ERROR"
    INVALID_STRUCTURED_RESULT = "INVALID_STRUCTURED_RESULT"
    PROVIDER_AUTH_ERROR = "PROVIDER_AUTH_ERROR"
    PROVIDER_CONFIGURATION_ERROR = "PROVIDER_CONFIGURATION_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    STORAGE_ERROR = "STORAGE_ERROR"


@dataclass(frozen=True)
class ProviderFailureProfile:
    retryable: bool
    fallback_eligible: bool


FAILURE_PROFILES: dict[ProviderFailureKind, ProviderFailureProfile] = {
    ProviderFailureKind.WORKER_UNAVAILABLE: ProviderFailureProfile(True, True),
    ProviderFailureKind.MODEL_UNAVAILABLE: ProviderFailureProfile(True, True),
    ProviderFailureKind.MODEL_MISMATCH: ProviderFailureProfile(False, False),
    ProviderFailureKind.JOB_QUEUE_TIMEOUT: ProviderFailureProfile(True, True),
    ProviderFailureKind.JOB_LEASE_EXPIRED: ProviderFailureProfile(True, True),
    ProviderFailureKind.LOCAL_INFERENCE_TIMEOUT: ProviderFailureProfile(True, True),
    ProviderFailureKind.LOCAL_INFERENCE_ERROR: ProviderFailureProfile(True, True),
    ProviderFailureKind.INVALID_STRUCTURED_RESULT: ProviderFailureProfile(False, False),
    ProviderFailureKind.PROVIDER_AUTH_ERROR: ProviderFailureProfile(False, False),
    ProviderFailureKind.PROVIDER_CONFIGURATION_ERROR: ProviderFailureProfile(False, False),
    ProviderFailureKind.INVALID_REQUEST: ProviderFailureProfile(False, False),
    ProviderFailureKind.STORAGE_ERROR: ProviderFailureProfile(False, False),
}


class ProviderFailure(ProviderUnavailableError):
    def __init__(
        self,
        *,
        kind: ProviderFailureKind,
        provider: str,
        safe_message: str,
        cause: Exception | None = None,
        trace_metadata: dict[str, int | None] | None = None,
    ) -> None:
        profile = FAILURE_PROFILES[kind]
        super().__init__(safe_message)
        self.kind = kind
        self.provider = provider
        self.retryable = profile.retryable
        self.fallback_eligible = profile.fallback_eligible
        self.public_error_code = kind.value
        self.safe_message = safe_message
        self.cause = cause
        self.trace_metadata = trace_metadata or {}


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