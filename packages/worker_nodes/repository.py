from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.persistence.models import WorkerNode


class WorkerNodeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, worker_id: str) -> WorkerNode | None:
        return self.session.scalar(select(WorkerNode).where(WorkerNode.worker_id == worker_id))

    def list(self) -> list[WorkerNode]:
        return list(self.session.scalars(select(WorkerNode).order_by(WorkerNode.worker_id)))

    def add(self, worker: WorkerNode) -> WorkerNode:
        self.session.add(worker)
        self.session.flush()
        return worker