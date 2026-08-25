"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from revenueguard_integrations.persistence import (
    create_database_engine,
    create_session_factory,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from revenueguard_api.config import get_settings
from revenueguard_api.persistence import (
    DatabaseMerchantWebhookResolver,
    DatabaseWebhookIngestionService,
)
from revenueguard_api.probes import DependencyProbe, probe_dependencies
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
) -> FastAPI:
    """Create an API instance with injectable dependency probes for tests."""

    settings = get_settings()
    application_engine: AsyncEngine | None = None
    if (
        merchant_webhook_resolver is None
        and webhook_ingestion_service is None
        and settings.razorpay_merchant_id is not None
        and settings.razorpay_webhook_secret is not None
        and settings.razorpay_webhook_secret.get_secret_value()
    ):
        application_engine = create_database_engine(settings.database_url)
        session_factory = create_session_factory(application_engine)
        merchant_webhook_resolver = DatabaseMerchantWebhookResolver(
            session_factory,
            configured_merchant_id=settings.razorpay_merchant_id,
            webhook_secret=settings.razorpay_webhook_secret.get_secret_value(),
        )
        webhook_ingestion_service = DatabaseWebhookIngestionService(
            session_factory,
            max_dispatch_attempts=settings.event_dispatch_max_attempts,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if application_engine is not None:
            await application_engine.dispose()

    application = FastAPI(
        title="RevenueGuard API",
        summary="Bounded revenue recovery control plane",
        description=(
            "Phase 2 accepts authenticated Razorpay events into a durable inbox. "
            "Financial and customer-contact actions remain disabled."
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
    application.include_router(system_router)
    application.include_router(webhook_router)
    return application


app = create_app()
