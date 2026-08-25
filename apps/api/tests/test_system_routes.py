from collections.abc import Awaitable, Callable

import pytest
from httpx import ASGITransport, AsyncClient
from revenueguard_api.config import Settings, get_settings
from revenueguard_api.main import create_app

Probe = Callable[[Settings], Awaitable[dict[str, bool]]]


def make_client(probe: Probe) -> AsyncClient:
    get_settings.cache_clear()
    transport = ASGITransport(app=create_app(dependency_probe=probe))
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def ready_probe() -> Probe:
    async def probe(_: Settings) -> dict[str, bool]:
        return {"postgres": True, "redis": True}

    return probe


async def test_health_is_live_without_dependency_probe(ready_probe: Probe) -> None:
    async with make_client(ready_probe) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "revenueguard-api",
        "version": "0.1.0",
    }


async def test_version_exposes_only_safe_deploy_metadata(ready_probe: Probe) -> None:
    async with make_client(ready_probe) as client:
        response = await client.get("/version")

    assert response.status_code == 200
    assert response.json() == {
        "service": "revenueguard-api",
        "version": "0.1.0",
        "environment": "development",
    }


async def test_readiness_is_ready_when_required_dependencies_are_ready(ready_probe: Probe) -> None:
    async with make_client(ready_probe) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "revenueguard-api",
        "dependencies": {"postgres": True, "redis": True},
    }


async def test_readiness_returns_503_and_names_failed_dependency() -> None:
    async def failing_probe(_: Settings) -> dict[str, bool]:
        return {"postgres": True, "redis": False}

    async with make_client(failing_probe) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "revenueguard-api",
        "dependencies": {"postgres": True, "redis": False},
    }


async def test_openapi_declares_all_system_endpoints(ready_probe: Probe) -> None:
    async with make_client(ready_probe) as client:
        paths = (await client.get("/openapi.json")).json()["paths"]

    assert {"/health", "/ready", "/version"}.issubset(paths)
