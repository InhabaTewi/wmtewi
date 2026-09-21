import pytest
from pydantic import BaseModel

from packages.providers.router import ProviderRouter


class Reply(BaseModel):
    text: str


class FakeProvider:
    def __init__(self, name: str, healthy: bool) -> None:
        self.name = name
        self.model_id = f"{name}-model"
        self.healthy = healthy

    async def health(self) -> bool:
        return self.healthy

    async def generate(self, messages, response_schema, trace_id):
        return response_schema(text=self.name)


@pytest.mark.asyncio
async def test_unhealthy_local_routes_to_external_api() -> None:
    external = FakeProvider("api", healthy=True)
    local = FakeProvider("local", healthy=False)

    response, route = await ProviderRouter(external=external, local=local).generate([], Reply, "trace")

    assert route.runtime_mode == "api"
    assert response.text == "api"


@pytest.mark.asyncio
async def test_healthy_local_routes_locally() -> None:
    external = FakeProvider("api", healthy=True)
    local = FakeProvider("local", healthy=True)

    response, route = await ProviderRouter(external=external, local=local).generate([], Reply, "trace")

    assert route.runtime_mode == "local"
    assert response.text == "local"