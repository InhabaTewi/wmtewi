import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.schemas.worker import WorkerHeartbeatRequest, WorkerRegisterRequest, WorkerStatus
from packages.worker_nodes.service import WorkerRegistryService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def postgres_url() -> str:
    url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not url:
        pytest.skip("set POSTGRES_INTEGRATION_URL to run PostgreSQL worker registry integration tests")
    if not url.startswith("postgresql+") or "test" not in url.lower():
        pytest.fail("POSTGRES_INTEGRATION_URL must target a dedicated PostgreSQL test database")
    return url


def test_postgres_worker_registration_survives_engine_restart(postgres_url: str) -> None:
    environment = os.environ | {"APP_ENV": "test", "DATABASE_URL": postgres_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )
    engine = create_engine(postgres_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        worker = WorkerRegistryService(session).register(
            WorkerRegisterRequest(
                worker_id="postgres-test-worker",
                capabilities=["llm.inference"],
                gpu_name="Test GPU",
                gpu_count=1,
                vram_total_mb=16384,
                vram_used_mb=1024,
                vram_free_mb=15360,
                loaded_model="qwen-base",
                model_version="base",
                model_alias="test",
            )
        )
        WorkerRegistryService(session).heartbeat(
            worker.worker_id,
            WorkerHeartbeatRequest(status=WorkerStatus.BUSY, capabilities=["llm.inference"], gpu_count=1),
        )
        session.commit()
    engine.dispose()

    restarted_engine = create_engine(postgres_url)
    restarted_factory = sessionmaker(bind=restarted_engine)
    with restarted_factory() as session:
        worker = WorkerRegistryService(session).get("postgres-test-worker")
        assert worker.status == WorkerStatus.BUSY
        assert worker.effective_status == WorkerStatus.BUSY
        assert worker.gpu_count == 1
    restarted_engine.dispose()