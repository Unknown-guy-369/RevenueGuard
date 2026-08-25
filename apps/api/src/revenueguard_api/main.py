"""FastAPI application factory."""

from fastapi import FastAPI

from revenueguard_api.config import get_settings
from revenueguard_api.probes import DependencyProbe, probe_dependencies
from revenueguard_api.routes.system import router as system_router


def create_app(dependency_probe: DependencyProbe = probe_dependencies) -> FastAPI:
    """Create an API instance with injectable dependency probes for tests."""

    settings = get_settings()
    application = FastAPI(
        title="RevenueGuard API",
        summary="Bounded revenue recovery control plane",
        description=(
            "Phase 1 exposes system endpoints only. "
            "Financial and customer-contact actions remain disabled."
        ),
        version=settings.app_version,
    )
    application.state.dependency_probe = dependency_probe
    application.include_router(system_router)
    return application


app = create_app()
