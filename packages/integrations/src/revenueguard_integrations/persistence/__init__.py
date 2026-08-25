"""PostgreSQL persistence boundary for durable event ingestion."""

from revenueguard_integrations.persistence.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from revenueguard_integrations.persistence.models import (
    Base,
    Customer,
    EventCorrelation,
    EventDispatch,
    Merchant,
    NormalizedEvent,
    Payment,
    Subscription,
    WebhookEvent,
)
from revenueguard_integrations.persistence.repositories import (
    DispatchFailureResult,
    EventIngestionRepository,
    WebhookInsertResult,
)

__all__ = [
    "AsyncSessionFactory",
    "Base",
    "Customer",
    "DispatchFailureResult",
    "EventCorrelation",
    "EventDispatch",
    "EventIngestionRepository",
    "Merchant",
    "NormalizedEvent",
    "Payment",
    "Subscription",
    "WebhookEvent",
    "WebhookInsertResult",
    "create_database_engine",
    "create_session_factory",
    "session_scope",
]
