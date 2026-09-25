from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from packages.schemas.worker import WorkerIdentifier


class InferenceJobStatus(str, Enum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class InferenceErrorCode(str, Enum):
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    JOB_EXPIRED = "JOB_EXPIRED"
    INFERENCE_TIMEOUT = "INFERENCE_TIMEOUT"
    INFERENCE_ERROR = "INFERENCE_ERROR"
    INVALID_RESULT = "INVALID_RESULT"
    MODEL_MISMATCH = "MODEL_MISMATCH"


class InferenceJobPayload(BaseModel):
    messages: list[dict[str, str]] = Field(min_length=1)
    response_schema: dict[str, Any]


class InferenceJobResult(BaseModel):
    structured_output: dict[str, Any]
    finish_reason: str | None = Field(default=None, max_length=64)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    inference_ms: int | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, max_length=256)
    model_version: str | None = Field(default=None, max_length=256)
    engine: str | None = Field(default=None, max_length=64)


class InferenceJobClaimRequest(BaseModel):
    worker_id: WorkerIdentifier
    wait_seconds: float = Field(default=0, ge=0, le=15)


class InferenceJobClaim(BaseModel):
    id: UUID
    request_id: UUID
    trace_id: UUID
    worker_id: str
    claim_token: str
    lease_expires_at: datetime
    payload: InferenceJobPayload
    model_alias: str | None = None
    model_version_requirement: str | None = None


class InferenceJobCompleteRequest(BaseModel):
    worker_id: WorkerIdentifier
    claim_token: str = Field(min_length=32, max_length=256)
    result: InferenceJobResult


class InferenceJobFailRequest(BaseModel):
    worker_id: WorkerIdentifier
    claim_token: str = Field(min_length=32, max_length=256)
    error_code: InferenceErrorCode
    error_message: str = Field(min_length=1, max_length=512)


class InferenceJobInfo(BaseModel):
    id: UUID
    request_id: UUID
    trace_id: UUID
    status: InferenceJobStatus
    target_worker_id: str
    model_alias: str | None
    model_version_requirement: str | None
    claimed_by_worker_id: str | None
    result: InferenceJobResult | None
    error_code: InferenceErrorCode | None
    error_message: str | None
    created_at: datetime
    queued_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    expires_at: datetime
    lease_expires_at: datetime | None