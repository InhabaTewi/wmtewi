import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from apps.gpu_worker.client import (
    CloudAuthenticationError,
    CloudProtocolError,
    CloudUnavailableError,
    WorkerCloudClient,
)
from apps.gpu_worker.config import WorkerSettings
from apps.gpu_worker.gpu_probe import GpuProbe, GpuProbeError
from apps.gpu_worker.local_model_client import LocalModelClient, LocalModelError
from apps.gpu_worker.state import WorkerState
from packages.schemas.worker import (
    WorkerCapability,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
    WorkerStatus,
)


Sleep = Callable[[float], Awaitable[None]]


class WorkerRuntime:
    def __init__(
        self,
        settings: WorkerSettings,
        client: WorkerCloudClient,
        gpu_probe: GpuProbe,
        *,
        local_model_client: LocalModelClient | None = None,
        sleeper: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.gpu_probe = gpu_probe
        self.local_model_client = local_model_client
        self.sleeper = sleeper
        self.jitter = jitter
        self.logger = logger or logging.getLogger(__name__)
        self.heartbeat_interval_seconds = settings.heartbeat_interval_seconds
        self._last_gpu_error: str | None = None

    async def diagnose(self) -> WorkerState:
        return await self._probe_state()

    async def run_once(self) -> None:
        state = await self._probe_state()
        response = await self.client.register(self._register_request(state))
        self.heartbeat_interval_seconds = self._clamp_interval(response.heartbeat_interval_seconds)
        await self.client.heartbeat(self.settings.worker_id, self._heartbeat_request(state))

    async def run(self, stop_event: asyncio.Event) -> None:
        registered = False
        retry_delay = 1.0
        self.logger.info("Worker starting: %s (%s)", self.settings.worker_id, self.settings.worker_name)
        while not stop_event.is_set():
            try:
                state = await self._probe_state()
                if not registered:
                    response = await self.client.register(self._register_request(state))
                    self.heartbeat_interval_seconds = self._clamp_interval(response.heartbeat_interval_seconds)
                    self.logger.info("Worker registration succeeded")
                    registered = True
                await self.client.heartbeat(self.settings.worker_id, self._heartbeat_request(state))
                self.logger.debug("Worker heartbeat succeeded")
                retry_delay = 1.0
                await self._wait_for_stop(stop_event, self.heartbeat_interval_seconds)
            except (CloudAuthenticationError, CloudProtocolError):
                raise
            except CloudUnavailableError:
                if registered:
                    self.logger.warning("Cloud disconnected; re-registering when connectivity returns")
                else:
                    self.logger.warning("Cloud unavailable; retrying registration")
                registered = False
                await self._wait_for_stop(stop_event, self._backoff_delay(retry_delay))
                retry_delay = min(retry_delay * 2, 30.0)
        self.logger.info("Worker shutdown requested")

    async def aclose(self) -> None:
        await self.client.aclose()
        if self.local_model_client is not None:
            await self.local_model_client.aclose()

    async def _probe_state(self) -> WorkerState:
        try:
            gpu = await asyncio.to_thread(self.gpu_probe.probe)
        except GpuProbeError as exc:
            detail = str(exc)
            if detail != self._last_gpu_error:
                self.logger.warning("GPU probe degraded: %s", detail)
                self._last_gpu_error = detail
            return WorkerState(status=WorkerStatus.DEGRADED, gpu=None, gpu_error=detail)
        if self._last_gpu_error is not None:
            self.logger.info("GPU probe recovered")
        self._last_gpu_error = None
        if self.local_model_client is None:
            return WorkerState(status=WorkerStatus.ONLINE, gpu=gpu)
        try:
            healthy = await self.local_model_client.health()
        except LocalModelError as exc:
            self.logger.warning("Local model probe degraded: %s", exc)
            healthy = False
        return WorkerState(
            status=WorkerStatus.ONLINE if healthy else WorkerStatus.DEGRADED,
            gpu=gpu,
            local_model_healthy=healthy,
        )

    def _register_request(self, state: WorkerState) -> WorkerRegisterRequest:
        return WorkerRegisterRequest(
            worker_id=self.settings.worker_id,
            status=WorkerStatus.REGISTERING,
            **self._metadata(state),
        )

    def _heartbeat_request(self, state: WorkerState) -> WorkerHeartbeatRequest:
        return WorkerHeartbeatRequest(status=state.status, **self._metadata(state))

    def _metadata(self, state: WorkerState) -> dict:
        metadata = {
            "capabilities": [WorkerCapability.LLM_INFERENCE],
            "loaded_model": self.settings.local_llm_source_model if state.local_model_healthy else None,
            "model_version": self.settings.local_llm_model_revision if state.local_model_healthy else None,
            "model_alias": "local-dev" if state.local_model_healthy else None,
        }
        if state.gpu is not None:
            metadata.update(
                gpu_name=state.gpu.gpu_name,
                gpu_count=state.gpu.gpu_count,
                vram_total_mb=state.gpu.vram_total_mb,
                vram_used_mb=state.gpu.vram_used_mb,
                vram_free_mb=state.gpu.vram_free_mb,
            )
        return metadata

    @staticmethod
    def _clamp_interval(server_interval: int) -> int:
        return min(60, max(5, server_interval))

    def _backoff_delay(self, retry_delay: float) -> float:
        return min(30.0, retry_delay + self.jitter() * min(1.0, retry_delay / 10))

    async def _wait_for_stop(self, stop_event: asyncio.Event, delay: float) -> None:
        stop_task = asyncio.create_task(stop_event.wait())
        delay_task = asyncio.create_task(self.sleeper(delay))
        done, pending = await asyncio.wait({stop_task, delay_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)