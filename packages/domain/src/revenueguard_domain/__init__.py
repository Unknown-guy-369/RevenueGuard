"""Framework-independent RevenueGuard domain package."""

from revenueguard_domain.events import (
    SCHEMA_VERSION,
    EventSource,
    NormalizedFailureCategory,
    RevenueRiskEvent,
)
from revenueguard_domain.version import __version__

__all__ = [
    "SCHEMA_VERSION",
    "EventSource",
    "NormalizedFailureCategory",
    "RevenueRiskEvent",
    "__version__",
]
