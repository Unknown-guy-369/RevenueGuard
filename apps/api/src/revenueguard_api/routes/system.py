"""Liveness, readiness, and version endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from revenueguard_api.config import Settings, get_settings
from revenueguard_api.probes import DependencyProbe
from revenueguard_api.schemas import (
    DependencyStatus,
    HealthResponse,
    ReadinessResponse,
    VersionResponse,
)

router = APIRouter(tags=["system"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDependency) -> HealthResponse:
    """Process liveness; it intentionally does not call dependencies."""

    return HealthResponse(status="ok", service=settings.service_name, version=settings.app_version)


@router.get("/version", response_model=VersionResponse)
async def version(settings: SettingsDependency) -> VersionResponse:
    """Expose deploy-identifying metadata without secrets."""

    return VersionResponse(
        service=settings.service_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    request: Request,
    response: Response,
    settings: SettingsDependency,
) -> ReadinessResponse:
    """Report whether the API's required infrastructure is reachable."""

    probe: DependencyProbe = request.app.state.dependency_probe
    result = await probe(settings)
    dependencies = DependencyStatus.model_validate(result)
    is_ready = dependencies.postgres and dependencies.redis
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        service=settings.service_name,
        dependencies=dependencies,
    )
