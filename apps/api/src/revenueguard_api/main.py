"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from revenueguard_integrations.persistence import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from revenueguard_api.config import get_settings
from revenueguard_api.dashboard import (
    DashboardQueryService,
    UnavailableDashboardQueryService,
)
from revenueguard_api.dashboard_persistence import DatabaseDashboardQueryService
from revenueguard_api.persistence import (
    DatabaseMerchantWebhookResolver,
    DatabaseWebhookIngestionService,
)
from revenueguard_api.probes import DependencyProbe, probe_dependencies
from revenueguard_api.routes.dashboard import router as dashboard_router
from revenueguard_api.routes.system import router as system_router
from revenueguard_api.routes.webhooks import router as webhook_router
from revenueguard_api.webhooks import (
    MerchantWebhookResolver,
    UnconfiguredMerchantResolver,
    UnconfiguredWebhookIngestionService,
    WebhookIngestionService,
)


def create_app(
    dependency_probe: DependencyProbe = probe_dependencies,
    merchant_webhook_resolver: MerchantWebhookResolver | None = None,
    webhook_ingestion_service: WebhookIngestionService | None = None,
    dashboard_query_service: DashboardQueryService | None = None,
) -> FastAPI:
    """Create an API instance with injectable dependency probes for tests."""

    settings = get_settings()
    application_engine: AsyncEngine | None = None
    session_factory: AsyncSessionFactory | None = None
    dashboard_configured = settings.dashboard_api_token is not None and bool(
        settings.dashboard_api_token.get_secret_value()
    )
    webhook_configured = (
        merchant_webhook_resolver is None
        and webhook_ingestion_service is None
        and settings.razorpay_merchant_id is not None
        and settings.razorpay_webhook_secret is not None
        and settings.razorpay_webhook_secret.get_secret_value()
    )
    if webhook_configured or (dashboard_query_service is None and dashboard_configured):
        application_engine = create_database_engine(settings.database_url)
        session_factory = create_session_factory(application_engine)
    if webhook_configured:
        if session_factory is None:
            raise AssertionError("webhook persistence session factory was not initialized")
        if settings.razorpay_merchant_id is None or settings.razorpay_webhook_secret is None:
            raise AssertionError("validated webhook configuration is incomplete")
        merchant_webhook_resolver = DatabaseMerchantWebhookResolver(
            session_factory,
            configured_merchant_id=settings.razorpay_merchant_id,
            webhook_secret=settings.razorpay_webhook_secret.get_secret_value(),
        )
        webhook_ingestion_service = DatabaseWebhookIngestionService(
            session_factory,
            max_dispatch_attempts=settings.event_dispatch_max_attempts,
        )
    if dashboard_query_service is None and dashboard_configured:
        if session_factory is None:
            raise AssertionError("dashboard persistence session factory was not initialized")
        dashboard_query_service = DatabaseDashboardQueryService(session_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if application_engine is not None:
            await application_engine.dispose()

    application = FastAPI(
        title="RevenueGuard API",
        summary="Bounded revenue recovery control plane",
        description=(
            "RevenueGuard accepts signed Test Mode events into a durable workflow, applies "
            "deterministic policy before outbox execution, verifies outcomes authoritatively, "
            "and exposes tenant-scoped operational evidence. Models never authorize actions."
        ),
        version=settings.app_version,
        lifespan=lifespan,
    )
    application.state.dependency_probe = dependency_probe
    application.state.merchant_webhook_resolver = (
        merchant_webhook_resolver or UnconfiguredMerchantResolver()
    )
    application.state.webhook_ingestion_service = (
        webhook_ingestion_service or UnconfiguredWebhookIngestionService()
    )
    application.state.dashboard_query_service = (
        dashboard_query_service or UnavailableDashboardQueryService()
    )
    application.include_router(system_router)
    application.include_router(dashboard_router)
    application.include_router(webhook_router)
    return application


app = create_app()
