"""Strict Razorpay webhook-to-domain event normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, cast

from revenueguard_domain.events import (
    EventSource,
    NormalizedFailureCategory,
    RevenueRiskEvent,
)

SUPPORTED_EVENT_TYPES: Final = frozenset(
    {
        "payment.authorized",
        "payment.captured",
        "payment.failed",
        "subscription.pending",
        "subscription.charged",
        "subscription.halted",
        "payment_link.paid",
        "payment_link.cancelled",
        "payment_link.expired",
    }
)

_PAYMENT_EVENTS: Final = frozenset({"payment.authorized", "payment.captured", "payment.failed"})
_SUBSCRIPTION_EVENTS: Final = frozenset(
    {"subscription.pending", "subscription.charged", "subscription.halted"}
)
_PAYMENT_LINK_EVENTS: Final = frozenset(
    {"payment_link.paid", "payment_link.cancelled", "payment_link.expired"}
)
_FAILURE_EVENTS: Final = frozenset(
    {"payment.failed", "subscription.pending", "subscription.halted"}
)


class RazorpayEventError(ValueError):
    """Base class for safe, machine-readable normalization failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class MalformedRazorpayEventError(RazorpayEventError):
    """The event does not satisfy the supported Razorpay payload shape."""

    def __init__(self, message: str) -> None:
        super().__init__("MALFORMED_RAZORPAY_EVENT", message)


class UnsupportedRazorpayEventError(RazorpayEventError):
    """The payload is valid JSON but its event type is not allowlisted."""

    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
        super().__init__(
            "UNSUPPORTED_RAZORPAY_EVENT",
            f"unsupported Razorpay event type: {event_type!r}",
        )


def normalize_razorpay_event(
    raw_body: bytes,
    *,
    merchant_id: str,
    provider_event_id: str,
    event_id: str,
    received_at: datetime,
    correlation_id: str,
    source_payload_reference: str,
    causation_id: str | None = None,
    source: EventSource = EventSource.RAZORPAY,
) -> RevenueRiskEvent:
    """Normalize one verified Razorpay webhook into the v1 domain contract.

    Signature verification intentionally remains a separate mandatory ingress
    step: this function accepts no secret and cannot claim that a body is
    authentic. ``merchant_id`` is trusted only from the caller's resolved
    tenant context; any provider ``account_id`` in the body is ignored.
    """

    document = _parse_document(raw_body)
    event_type = _required_string(document, "event")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise UnsupportedRazorpayEventError(event_type)
    if _required_string(document, "entity") != "event":
        raise MalformedRazorpayEventError("top-level entity must be 'event'")

    payload = _required_mapping(document, "payload")
    payment = _optional_entity(payload, "payment")
    subscription = _optional_entity(payload, "subscription")
    payment_link = _optional_entity(payload, "payment_link")

    if event_type in _PAYMENT_EVENTS and payment is None:
        raise MalformedRazorpayEventError(f"{event_type} requires payload.payment.entity")
    if event_type in _SUBSCRIPTION_EVENTS and subscription is None:
        raise MalformedRazorpayEventError(f"{event_type} requires payload.subscription.entity")
    if event_type in _PAYMENT_LINK_EVENTS and payment_link is None:
        raise MalformedRazorpayEventError(f"{event_type} requires payload.payment_link.entity")

    financial_entity = payment if payment is not None else payment_link
    if financial_entity is None:
        raise MalformedRazorpayEventError(f"{event_type} requires a payment or payment-link amount")

    amount_minor = _required_nonnegative_integer(financial_entity, "amount")
    currency = _required_currency(financial_entity)
    failure_code, failure_category = _failure_details(event_type, payment)

    occurred_at = _provider_timestamp(document, "created_at")
    customer_id = _first_identifier(payment, subscription, payment_link, field="customer_id")

    return RevenueRiskEvent(
        event_id=event_id,
        merchant_id=merchant_id,
        source=source,
        source_event_id=provider_event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        received_at=received_at,
        customer_id=customer_id,
        payment_id=_identifier(payment, "id"),
        order_id=_identifier(payment, "order_id"),
        subscription_id=_identifier(subscription, "id"),
        invoice_id=_identifier(payment, "invoice_id"),
        payment_link_id=_identifier(payment_link, "id"),
        amount_minor=amount_minor,
        currency=currency,
        failure_code=failure_code,
        normalized_failure_category=failure_category,
        correlation_id=correlation_id,
        causation_id=causation_id,
        source_payload_reference=source_payload_reference,
    )


def _parse_document(raw_body: bytes) -> Mapping[str, object]:
    if not isinstance(raw_body, bytes):
        raise TypeError("raw_body must be bytes")
    try:
        decoded = raw_body.decode("utf-8")
        parsed = cast(object, json.loads(decoded))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedRazorpayEventError("body must be a UTF-8 JSON object") from error
    if not isinstance(parsed, dict):
        raise MalformedRazorpayEventError("top-level JSON value must be an object")
    return cast(Mapping[str, object], parsed)


def _required_mapping(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    candidate = value.get(field)
    if not isinstance(candidate, dict):
        raise MalformedRazorpayEventError(f"{field} must be an object")
    return cast(Mapping[str, object], candidate)


def _required_string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or not candidate:
        raise MalformedRazorpayEventError(f"{field} must be a non-empty string")
    return candidate


def _optional_entity(
    payload: Mapping[str, object], entity_name: str
) -> Mapping[str, object] | None:
    wrapper = payload.get(entity_name)
    if wrapper is None:
        return None
    if not isinstance(wrapper, dict):
        raise MalformedRazorpayEventError(f"payload.{entity_name} must be an object")
    entity = cast(Mapping[str, object], wrapper).get("entity")
    if not isinstance(entity, dict):
        raise MalformedRazorpayEventError(f"payload.{entity_name}.entity must be an object")
    typed_entity = cast(Mapping[str, object], entity)
    declared_type = typed_entity.get("entity")
    if declared_type != entity_name:
        raise MalformedRazorpayEventError(
            f"payload.{entity_name}.entity.entity must be {entity_name!r}"
        )
    return typed_entity


def _identifier(entity: Mapping[str, object] | None, field: str) -> str | None:
    if entity is None or field not in entity or entity[field] is None:
        return None
    value = entity[field]
    if not isinstance(value, str) or not value:
        raise MalformedRazorpayEventError(f"entity field {field} must be a non-empty string")
    return value


def _first_identifier(
    *entities: Mapping[str, object] | None,
    field: str,
) -> str | None:
    for entity in entities:
        identifier = _identifier(entity, field)
        if identifier is not None:
            return identifier
    return None


def _required_nonnegative_integer(entity: Mapping[str, object], field: str) -> int:
    value = entity.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedRazorpayEventError(f"{field} must be a non-negative integer")
    return value


def _required_currency(entity: Mapping[str, object]) -> str:
    value = _required_string(entity, "currency")
    if len(value) != 3 or not value.isascii() or not value.isalpha() or not value.isupper():
        raise MalformedRazorpayEventError("currency must be a three-letter uppercase ISO code")
    return value


def _provider_timestamp(document: Mapping[str, object], field: str) -> datetime:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedRazorpayEventError(f"{field} must be a non-negative Unix timestamp")
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise MalformedRazorpayEventError(f"{field} is outside the supported range") from error


def _failure_details(
    event_type: str,
    payment: Mapping[str, object] | None,
) -> tuple[str | None, NormalizedFailureCategory]:
    if event_type not in _FAILURE_EVENTS:
        return None, NormalizedFailureCategory.NONE
    if payment is None:
        return None, NormalizedFailureCategory.UNKNOWN

    code = _identifier(payment, "error_code")
    reason = _identifier(payment, "error_reason")
    description = _identifier(payment, "error_description")
    evidence = " ".join(item.lower() for item in (code, reason, description) if item)

    mappings = (
        (("insufficient_funds", "insufficient fund"), NormalizedFailureCategory.INSUFFICIENT_FUNDS),
        (
            ("expired_card", "card_expired", "expired payment"),
            NormalizedFailureCategory.EXPIRED_PAYMENT_METHOD,
        ),
        (
            ("authentication", "incorrect_otp", "otp_failed"),
            NormalizedFailureCategory.AUTHENTICATION_FAILURE,
        ),
        (
            ("issuer_down", "bank_down", "issuer_unavailable"),
            NormalizedFailureCategory.ISSUER_UNAVAILABLE,
        ),
        (("gateway_down", "gateway_unavailable"), NormalizedFailureCategory.GATEWAY_UNAVAILABLE),
        (("customer_action_required",), NormalizedFailureCategory.CUSTOMER_ACTION_REQUIRED),
        (("dispute",), NormalizedFailureCategory.DISPUTE),
    )
    for tokens, category in mappings:
        if any(token in evidence for token in tokens):
            return code or reason, category
    return code or reason, NormalizedFailureCategory.UNKNOWN
