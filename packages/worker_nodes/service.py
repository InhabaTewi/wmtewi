from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from packages.persistence.config import settings
from packages.persistence.models import WorkerNode
from packages.schemas.worker import (
    WorkerHeartbeatRequest,
    WorkerInfo,
    WorkerRegisterRequest,
    WorkerStatus,
)
from packages.worker_nodes.repository import WorkerNodeRepository


class WorkerNotFoundError(Exception):
    pass


class WorkerRegistryService:
    def __init__(self, session: Session, heartbeat_timeout_seconds: int | None = None) -> None:
        self.session = session
        self.repository = WorkerNodeRepository(session)
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds or settings.worker_heartbeat_timeout_seconds

    def register(self, request: WorkerRegisterRequest, now: datetime | None = None) -> WorkerInfo:
        timestamp = now or datetime.now(UTC)
        worker = self.repository.get(request.worker_id)
        if worker is None:
            worker = WorkerNode(worker_id=request.worker_id, node_name=request.worker_id)
            self.repository.add(worker)
        self._apply_metadata(worker, request, timestamp)
        self.session.flush()
        return self._to_info(worker, timestamp)

    def heartbeat(self, worker_id: str, request: WorkerHeartbeatRequest, now: datetime | None = None) -> WorkerInfo:
        worker = self.repository.get(worker_id)
        if worker is None:
            raise WorkerNotFoundError(worker_id)
        timestamp = now or datetime.now(UTC)
        self._apply_metadata(worker, request, timestamp)
        self.session.flush()
        return self._to_info(worker, timestamp)

    def get(self, worker_id: str, now: datetime | None = None) -> WorkerInfo:
        worker = self.repository.get(worker_id)
        if worker is None:
            raise WorkerNotFoundError(worker_id)
        return self._to_info(worker, now or datetime.now(UTC))

    def list(self, now: datetime | None = None) -> list[WorkerInfo]:
        timestamp = now or datetime.now(UTC)
        return [self._to_info(worker, timestamp) for worker in self.repository.list()]

    @staticmethod
    def _apply_metadata(worker: WorkerNode, request: WorkerRegisterRequest | WorkerHeartbeatRequest, timestamp: datetime) -> None:
        worker.status = request.status.value
        worker.capabilities = [capability.value for capability in request.capabilities]
        worker.gpu_name = request.gpu_name
        worker.gpu_count = request.gpu_count
        worker.vram_total_mb = request.vram_total_mb
        worker.vram_used_mb = request.vram_used_mb
        worker.vram_free_mb = request.vram_free_mb
        worker.loaded_model = request.loaded_model
        worker.model_version = request.model_version
        worker.model_alias = request.model_alias
        worker.last_heartbeat_at = timestamp
        worker.last_seen_at = timestamp

    def _to_info(self, worker: WorkerNode, now: datetime) -> WorkerInfo:
        reported_status = WorkerStatus(worker.status)
        deadline = now - timedelta(seconds=self.heartbeat_timeout_seconds)
        heartbeat_at = worker.last_heartbeat_at
        if heartbeat_at is not None and heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=UTC)
        effective_status = (
            WorkerStatus.OFFLINE
            if heartbeat_at is None or heartbeat_at < deadline
            else reported_status
        )
        return WorkerInfo(
            id=worker.id,
            worker_id=worker.worker_id,
            status=reported_status,
            effective_status=effective_status,
            capabilities=worker.capabilities or [],
            gpu_name=worker.gpu_name,
            gpu_count=worker.gpu_count,
            vram_total_mb=worker.vram_total_mb,
            vram_used_mb=worker.vram_used_mb,
            vram_free_mb=worker.vram_free_mb,
            loaded_model=worker.loaded_model,
            model_version=worker.model_version,
            model_alias=worker.model_alias,
            last_heartbeat_at=worker.last_heartbeat_at,
            last_seen_at=worker.last_seen_at,
            created_at=worker.created_at,
            updated_at=worker.updated_at,
        )