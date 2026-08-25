"""Concrete PostgreSQL adapters for the webhook application protocols."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from revenueguard_integrations.persistence import (
    AsyncSessionFactory,
    EventIngestionRepository,
    Merchant,
    session_scope,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from revenueguard_api.webhooks import (
    IngestionDisposition,
    InvalidSignatureRecord,
    ResolvedMerchant,
    VerifiedRazorpayWebhook,
    WebhookPersistenceError,
)


class DatabaseMerchantWebhookResolver:
    """Resolve one configured Test Mode merchant against authoritative database state."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        configured_merchant_id: str,
        webhook_secret: str,
    ) -> None:
        self._session_factory = session_factory
        self._configured_merchant_id = configured_merchant_id
        self._webhook_secret = webhook_secret

    async def resolve(self, routing_identifier: str) -> ResolvedMerchant | None:
        if routing_identifier != self._configured_merchant_id or not self._webhook_secret:
            return None
        try:
            async with self._session_factory() as session:
                merchant_id = await session.scalar(
                    select(Merchant.id).where(
                        Merchant.id == self._configured_merchant_id,
                        Merchant.status == "ACTIVE",
                    )
                )
        except SQLAlchemyError as error:
            raise WebhookPersistenceError("merchant resolution failed") from error
        if merchant_id is None:
            return None
        return ResolvedMerchant(merchant_id=merchant_id, webhook_secret=self._webhook_secret)


class DatabaseWebhookIngestionService:
    """Commit accepted inbox events and rejection metadata in explicit transactions."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        max_dispatch_attempts: int,
    ) -> None:
        self._session_factory = session_factory
        self._max_dispatch_attempts = max_dispatch_attempts

    async def ingest_verified(self, webhook: VerifiedRazorpayWebhook) -> IngestionDisposition:
        event_type = _string_or_default(webhook.payload.get("event"), "UNKNOWN")
        event_id = str(uuid4())
        try:
            async with session_scope(self._session_factory) as session:
                result = await EventIngestionRepository(session).record_webhook(
                    event_id=event_id,
                    merchant_id=webhook.merchant_id,
                    provider="RAZORPAY",
                    provider_event_id=webhook.provider_event_id,
                    event_type=event_type,
                    entity_id=_primary_entity_id(webhook.payload),
                    raw_body=webhook.raw_body,
                    raw_payload=webhook.payload,
                    occurred_at=_provider_occurred_at(webhook.payload),
                    received_at=webhook.received_at,
                    correlation_id=_correlation_id(webhook.merchant_id, webhook.provider_event_id),
                    max_dispatch_attempts=self._max_dispatch_attempts,
                )
        except SQLAlchemyError as error:
            raise WebhookPersistenceError("webhook inbox commit failed") from error
        if result.created:
            return IngestionDisposition.ACCEPTED
        return IngestionDisposition.DUPLICATE

    async def record_invalid_signature(self, record: InvalidSignatureRecord) -> None:
        try:
            async with session_scope(self._session_factory) as session:
                await EventIngestionRepository(session).record_invalid_webhook(
                    event_id=str(uuid4()),
                    merchant_id=record.merchant_id,
                    provider="RAZORPAY",
                    provider_event_id=record.provider_event_id,
                    raw_payload_sha256=record.payload_sha256,
                    received_at=record.received_at,
                    correlation_id=_correlation_id(record.merchant_id, record.provider_event_id),
                    failure_code="SIGNATURE_MISMATCH",
                )
        except SQLAlchemyError as error:
            raise WebhookPersistenceError("invalid-signature audit commit failed") from error


def _correlation_id(merchant_id: str, provider_event_id: str) -> str:
    material = f"{merchant_id}\0{provider_event_id}".encode()
    return f"corr_{sha256(material).hexdigest()[:48]}"


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _provider_occurred_at(payload: dict[str, Any]) -> datetime | None:
    timestamp = payload.get("created_at")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except OverflowError, OSError, ValueError:
        return None


def _primary_entity_id(payload: dict[str, Any]) -> str | None:
    wrapped_payload = payload.get("payload")
    if not isinstance(wrapped_payload, dict):
        return None
    for entity_type in ("payment", "subscription", "payment_link", "invoice"):
        wrapper = wrapped_payload.get(entity_type)
        if not isinstance(wrapper, dict):
            continue
        entity = wrapper.get("entity")
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("id")
        if isinstance(entity_id, str) and entity_id:
            return entity_id
    return None
