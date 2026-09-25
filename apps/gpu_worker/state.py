from dataclasses import dataclass

from apps.gpu_worker.gpu_probe import GpuMetadata
from packages.schemas.worker import WorkerStatus


@dataclass(frozen=True)
class WorkerState:
    status: WorkerStatus
    gpu: GpuMetadata | None
    gpu_error: str | None = None
    local_model_healthy: bool = False