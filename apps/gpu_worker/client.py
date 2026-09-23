from urllib.parse import quote

import httpx
from pydantic import ValidationError

from apps.gpu_worker.config import WorkerSettings
from packages.schemas.worker import (
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
)


class CloudClientError(RuntimeError):
    pass


class CloudUnavailableError(CloudClientError):
    pass


class CloudAuthenticationError(CloudClientError):
    pass


class CloudProtocolError(CloudClientError):
    pass


class CloudResponseError(CloudClientError):
    pass


class WorkerCloudClient:
    def __init__(
        self,
        settings: WorkerSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = settings.cloud_base_url.rstrip("/")
        self.timeout = httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_read_timeout,
            pool=settings.http_connect_timeout,
        )
        self._client = client or httpx.AsyncClient(
            timeout=self.timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.service_token.get_secret_value()}"},
        )
        self._owns_client = client is None

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def register(self, request: WorkerRegisterRequest) -> WorkerRegisterResponse:
        response = await self._post("api/workers/register", request.model_dump(mode="json"))
        return self._validate_response(response, WorkerRegisterResponse)

    async def heartbeat(
        self, worker_id: str, request: WorkerHeartbeatRequest
    ) -> WorkerHeartbeatResponse:
        path = f"api/workers/{quote(worker_id, safe='._-')}/heartbeat"
        response = await self._post(path, request.model_dump(mode="json"))
        return self._validate_response(response, WorkerHeartbeatResponse)

    async def _post(self, path: str, payload: dict) -> httpx.Response:
        try:
            response = await self._client.post(self._url(path), json=payload)
        except httpx.TimeoutException as exc:
            raise CloudUnavailableError("Cloud request timed out") from exc
        except httpx.RequestError as exc:
            raise CloudUnavailableError("Cloud request failed") from exc
        if response.status_code == 401:
            raise CloudAuthenticationError("Cloud rejected the worker credentials")
        if response.status_code == 404:
            raise CloudProtocolError("Cloud Worker Registry endpoint is unavailable")
        if response.status_code in {502, 503, 504}:
            raise CloudUnavailableError("Cloud is temporarily unavailable")
        if not response.is_success:
            raise CloudResponseError(f"Cloud returned HTTP {response.status_code}")
        return response

    @staticmethod
    def _validate_response(response: httpx.Response, schema: type[WorkerRegisterResponse | WorkerHeartbeatResponse]):
        try:
            return schema.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CloudResponseError("Cloud returned an invalid Worker Registry response") from exc

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()