from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class WorkerStatus(str, Enum):
    REGISTERING = "REGISTERING"
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    TRAINING = "TRAINING"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class WorkerCapability(str, Enum):
    LLM_INFERENCE = "llm.inference"
    LLM_TRAINING = "llm.training"
    ASR = "asr"
    TTS = "tts"
    VISION = "vision"


WorkerIdentifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
NonNegativeInteger = Annotated[int, Field(ge=0)]


class WorkerMetadata(BaseModel):
    status: WorkerStatus = WorkerStatus.ONLINE
    capabilities: list[WorkerCapability] = Field(default_factory=list)
    gpu_name: str | None = Field(default=None, max_length=256)
    gpu_count: NonNegativeInteger | None = None
    vram_total_mb: NonNegativeInteger | None = None
    vram_used_mb: NonNegativeInteger | None = None
    vram_free_mb: NonNegativeInteger | None = None
    loaded_model: str | None = Field(default=None, max_length=256)
    model_version: str | None = Field(default=None, max_length=256)
    model_alias: str | None = Field(default=None, max_length=128)

    @field_validator("capabilities")
    @classmethod
    def reject_duplicate_capabilities(cls, value: list[WorkerCapability]) -> list[WorkerCapability]:
        if len(value) != len(set(value)):
            raise ValueError("capabilities must not contain duplicates")
        return value


class WorkerRegisterRequest(WorkerMetadata):
    worker_id: WorkerIdentifier


class WorkerHeartbeatRequest(WorkerMetadata):
    pass


class WorkerInfo(WorkerMetadata):
    id: UUID
    worker_id: str
    effective_status: WorkerStatus
    last_heartbeat_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkerRegisterResponse(BaseModel):
    worker: WorkerInfo
    heartbeat_interval_seconds: int
    server_time: datetime


class WorkerHeartbeatResponse(WorkerRegisterResponse):
    pass