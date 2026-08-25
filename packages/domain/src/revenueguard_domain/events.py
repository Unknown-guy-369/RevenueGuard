"""Versioned, provider-neutral revenue-risk event contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

SCHEMA_VERSION: Final = "1.0"
_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")


class EventSource(StrEnum):
    """Origin of a normalized revenue-risk event."""

    RAZORPAY = "RAZORPAY"
    MERCHANT = "MERCHANT"
    SYNTHETIC = "SYNTHETIC"


class NormalizedFailureCategory(StrEnum):
    """Provider-neutral failure families supported by the v1 contract."""

    NONE = "NONE"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    EXPIRED_PAYMENT_METHOD = "EXPIRED_PAYMENT_METHOD"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    ISSUER_UNAVAILABLE = "ISSUER_UNAVAILABLE"
    GATEWAY_UNAVAILABLE = "GATEWAY_UNAVAILABLE"
    CUSTOMER_ACTION_REQUIRED = "CUSTOMER_ACTION_REQUIRED"
    DISPUTE = "DISPUTE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True, kw_only=True)
class RevenueRiskEvent:
    """Canonical immutable event handed from provider adapters to the domain.

    The adapter, rather than the webhook body, supplies ``merchant_id``. This
    keeps tenant resolution at the authenticated ingress boundary and prevents
    provider payload fields from selecting a RevenueGuard tenant.
    """

    event_id: str
    merchant_id: str
    source: EventSource
    source_event_id: str
    event_type: str
    occurred_at: datetime
    received_at: datetime
    amount_minor: int
    currency: str
    normalized_failure_category: NormalizedFailureCategory
    correlation_id: str
    source_payload_reference: str
    customer_id: str | None = None
    payment_id: str | None = None
    order_id: str | None = None
    subscription_id: str | None = None
    invoice_id: str | None = None
    payment_link_id: str | None = None
    failure_code: str | None = None
    causation_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Reject invalid domain values at the provider boundary."""

        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported RevenueRiskEvent schema: {self.schema_version!r}")

        required_limits = {
            "event_id": (self.event_id, 128),
            "merchant_id": (self.merchant_id, 128),
            "source_event_id": (self.source_event_id, 256),
            "event_type": (self.event_type, 128),
            "correlation_id": (self.correlation_id, 128),
            "source_payload_reference": (self.source_payload_reference, 512),
        }
        for field_name, (required_value, maximum) in required_limits.items():
            _validate_identifier(field_name, required_value, maximum)

        optional_limits = {
            "customer_id": (self.customer_id, 128),
            "payment_id": (self.payment_id, 128),
            "order_id": (self.order_id, 128),
            "subscription_id": (self.subscription_id, 128),
            "invoice_id": (self.invoice_id, 128),
            "payment_link_id": (self.payment_link_id, 128),
            "failure_code": (self.failure_code, 128),
            "causation_id": (self.causation_id, 128),
        }
        for field_name, (optional_value, maximum) in optional_limits.items():
            if optional_value is not None:
                _validate_identifier(field_name, optional_value, maximum)

        if not isinstance(self.source, EventSource):
            raise TypeError("source must be an EventSource")
        if not isinstance(self.normalized_failure_category, NormalizedFailureCategory):
            raise TypeError("normalized_failure_category must be a NormalizedFailureCategory")

        if not any(
            (
                self.customer_id,
                self.payment_id,
                self.subscription_id,
                self.invoice_id,
                self.payment_link_id,
            )
        ):
            raise ValueError("a RevenueRiskEvent requires at least one subject reference")
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be an integer")
        if self.amount_minor < 0:
            raise ValueError("amount_minor cannot be negative")
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase ISO code")

        object.__setattr__(self, "occurred_at", _as_utc("occurred_at", self.occurred_at))
        object.__setattr__(self, "received_at", _as_utc("received_at", self.received_at))

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible v1 contract representation."""

        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "merchant_id": self.merchant_id,
            "source": self.source.value,
            "source_event_id": self.source_event_id,
            "event_type": self.event_type,
            "occurred_at": _format_datetime(self.occurred_at),
            "received_at": _format_datetime(self.received_at),
            "customer_id": self.customer_id,
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "subscription_id": self.subscription_id,
            "invoice_id": self.invoice_id,
            "payment_link_id": self.payment_link_id,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "failure_code": self.failure_code,
            "normalized_failure_category": self.normalized_failure_category.value,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "source_payload_reference": self.source_payload_reference,
        }


def _validate_identifier(field_name: str, value: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or len(value) > maximum:
        raise ValueError(f"{field_name} must contain between 1 and {maximum} characters")


def _as_utc(field_name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
