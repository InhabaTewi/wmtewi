import argparse
import asyncio
import logging
import signal
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from apps.gpu_worker.client import (
    CloudAuthenticationError,
    CloudProtocolError,
    WorkerCloudClient,
)
from apps.gpu_worker.config import WorkerSettings
from apps.gpu_worker.gpu_probe import NvidiaSmiGpuProbe
from apps.gpu_worker.local_model_client import LocalModelClient
from apps.gpu_worker.runtime import WorkerRuntime


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the outbound Inaba local GPU Worker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Register and send one heartbeat, then exit")
    group.add_argument("--diagnose", action="store_true", help="Validate local configuration and GPU access only")
    parser.add_argument(
        "--shutdown-file",
        type=Path,
        help="Exit gracefully when this local file is created",
    )
    return parser.parse_args(argv)


def create_runtime(settings: WorkerSettings) -> WorkerRuntime:
    probe = NvidiaSmiGpuProbe(settings.gpu_index)
    local_model_client = None
    if settings.local_llm_enabled:
        assert settings.local_llm_api_key is not None
        local_model_client = LocalModelClient(
            base_url=settings.local_llm_base_url,
            api_key=settings.local_llm_api_key,
            model_id=settings.local_llm_model,
            connect_timeout=settings.http_connect_timeout,
            read_timeout=settings.http_read_timeout,
        )
    return WorkerRuntime(settings, WorkerCloudClient(settings), probe, local_model_client=local_model_client)


async def run_worker(args: argparse.Namespace, settings: WorkerSettings) -> int:
    runtime = create_runtime(settings)
    try:
        if args.diagnose:
            state = await runtime.diagnose()
            print(f"Worker ID: {settings.worker_id}")
            print(f"Cloud host: {settings.cloud_host}")
            if state.gpu is None:
                print(f"GPU: unavailable ({state.gpu_error})")
                return 1
            print(f"GPU: {state.gpu.gpu_name}")
            print(f"VRAM: {state.gpu.vram_total_mb} MB total")
            return 0
        if args.once:
            await runtime.run_once()
            return 0

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            loop.call_soon_threadsafe(stop_event.set)

        async def watch_shutdown_file(path: Path) -> None:
            while not stop_event.is_set():
                if path.exists():
                    logging.getLogger(__name__).info("Worker shutdown file detected")
                    stop_event.set()
                    return
                await asyncio.sleep(0.2)

        previous_handlers: dict[signal.Signals, object] = {}
        for signal_type in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_type] = signal.getsignal(signal_type)
            signal.signal(signal_type, lambda _signum, _frame: request_stop())
        shutdown_watcher = (
            asyncio.create_task(watch_shutdown_file(args.shutdown_file)) if args.shutdown_file is not None else None
        )
        try:
            await runtime.run(stop_event)
        finally:
            if shutdown_watcher is not None:
                shutdown_watcher.cancel()
                await asyncio.gather(shutdown_watcher, return_exceptions=True)
            for signal_type, handler in previous_handlers.items():
                signal.signal(signal_type, handler)
        return 0
    finally:
        await runtime.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = WorkerSettings()
    except ValidationError:
        logging.getLogger(__name__).error("Worker configuration is invalid")
        return 2
    try:
        return asyncio.run(run_worker(parse_args(argv), settings))
    except CloudAuthenticationError:
        logging.getLogger(__name__).error("Worker authentication failed")
        return 3
    except CloudProtocolError:
        logging.getLogger(__name__).error("Cloud Worker Registry protocol is incompatible")
        return 4
    except KeyboardInterrupt:
        return 130