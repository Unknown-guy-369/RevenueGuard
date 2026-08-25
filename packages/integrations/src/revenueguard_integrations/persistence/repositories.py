"""Transactional repositories for the Phase 2 ingestion pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, and_, case, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from revenueguard_integrations.persistence.models import (
    Customer,
    EventCorrelation,
    EventDispatch,
    Merchant,
    NormalizedEvent,
    Payment,
    Subscription,
    WebhookEvent,
)
from revenueguard_integrations.persistence.status_ordering import PROVIDER_STATUS_PRECEDENCE


@dataclass(frozen=True, slots=True)
class WebhookInsertResult:
    """Result of an idempotent verified-webhook insert."""

    event: WebhookEvent
    created: bool
    dispatch_id: str | None


@dataclass(frozen=True, slots=True)
class DispatchFailureResult:
    """Outcome of applying the bounded retry policy to one dispatch."""

    dispatch_id: str
    state: str
    attempt_count: int


class EventIngestionRepository:
    """Keep event persistence and dispatch-state changes inside caller-owned transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_merchant(
        self,
        *,
        merchant_id: str,
        display_name: str,
        provider: str = "RAZORPAY",
        provider_account_id: str | None = None,
        status: str = "ACTIVE",
    ) -> Merchant:
        statement = (
            insert(Merchant)
            .values(
                id=merchant_id,
                display_name=display_name,
                provider=provider,
                provider_account_id=provider_account_id,
                status=status,
            )
            .on_conflict_do_update(
                index_elements=[Merchant.id],
                set_={
                    "display_name": display_name,
                    "provider_account_id": provider_account_id,
                    "status": status,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Merchant)
        )
        return (await self._session.scalars(statement)).one()

    async def upsert_customer(
        self,
        *,
        merchant_id: str,
        customer_id: str,
        provider_customer_id: str | None,
        provider_updated_at: datetime | None,
    ) -> Customer:
        insert_statement = insert(Customer).values(
            merchant_id=merchant_id,
            id=customer_id,
            provider_customer_id=provider_customer_id,
            provider_updated_at=provider_updated_at,
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[Customer.merchant_id, Customer.id],
            set_={
                "provider_customer_id": insert_statement.excluded.provider_customer_id,
                "provider_updated_at": insert_statement.excluded.provider_updated_at,
                "updated_at": datetime.now(UTC),
            },
            where=or_(
                Customer.provider_updated_at.is_(None),
                and_(
                    insert_statement.excluded.provider_updated_at.is_not(None),
                    insert_statement.excluded.provider_updated_at >= Customer.provider_updated_at,
                ),
            ),
        ).returning(Customer)
        result = (await self._session.scalars(statement)).one_or_none()
        if result is not None:
            return result
        return await self._get_customer(merchant_id, customer_id)

    async def upsert_payment(
        self,
        *,
        merchant_id: str,
        payment_id: str,
        provider_payment_id: str,
        customer_id: str | None,
        order_id: str | None,
        amount_minor: int,
        currency: str,
        status: str,
        provider_occurred_at: datetime,
        provider_updated_at: datetime | None,
    ) -> Payment:
        insert_statement = insert(Payment).values(
            merchant_id=merchant_id,
            id=payment_id,
            provider_payment_id=provider_payment_id,
            customer_id=customer_id,
            order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            status=status,
            provider_occurred_at=provider_occurred_at,
            provider_updated_at=provider_updated_at,
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[Payment.merchant_id, Payment.id],
            set_={
                "provider_payment_id": insert_statement.excluded.provider_payment_id,
                "customer_id": insert_statement.excluded.customer_id,
                "order_id": insert_statement.excluded.order_id,
                "amount_minor": insert_statement.excluded.amount_minor,
                "currency": insert_statement.excluded.currency,
                "status": insert_statement.excluded.status,
                "provider_occurred_at": insert_statement.excluded.provider_occurred_at,
                "provider_updated_at": insert_statement.excluded.provider_updated_at,
                "updated_at": datetime.now(UTC),
            },
            where=_provider_update_is_authoritative(
                current_status=Payment.status,
                current_updated_at=Payment.provider_updated_at,
                excluded_status=insert_statement.excluded.status,
                excluded_updated_at=insert_statement.excluded.provider_updated_at,
            ),
        ).returning(Payment)
        result = (await self._session.scalars(statement)).one_or_none()
        if result is not None:
            return result
        return await self._get_payment(merchant_id, payment_id)

    async def upsert_subscription(
        self,
        *,
        merchant_id: str,
        subscription_id: str,
        provider_subscription_id: str,
        customer_id: str | None,
        amount_minor: int,
        currency: str,
        status: str,
        provider_occurred_at: datetime,
        provider_updated_at: datetime | None,
    ) -> Subscription:
        insert_statement = insert(Subscription).values(
            merchant_id=merchant_id,
            id=subscription_id,
            provider_subscription_id=provider_subscription_id,
            customer_id=customer_id,
            amount_minor=amount_minor,
            currency=currency,
            status=status,
            provider_occurred_at=provider_occurred_at,
            provider_updated_at=provider_updated_at,
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[Subscription.merchant_id, Subscription.id],
            set_={
                "provider_subscription_id": insert_statement.excluded.provider_subscription_id,
                "customer_id": insert_statement.excluded.customer_id,
                "amount_minor": insert_statement.excluded.amount_minor,
                "currency": insert_statement.excluded.currency,
                "status": insert_statement.excluded.status,
                "provider_occurred_at": insert_statement.excluded.provider_occurred_at,
                "provider_updated_at": insert_statement.excluded.provider_updated_at,
                "updated_at": datetime.now(UTC),
            },
            where=_provider_update_is_authoritative(
                current_status=Subscription.status,
                current_updated_at=Subscription.provider_updated_at,
                excluded_status=insert_statement.excluded.status,
                excluded_updated_at=insert_statement.excluded.provider_updated_at,
            ),
        ).returning(Subscription)
        result = (await self._session.scalars(statement)).one_or_none()
        if result is not None:
            return result
        return await self._get_subscription(merchant_id, subscription_id)

    async def record_webhook(
        self,
        *,
        event_id: str,
        merchant_id: str,
        provider: str,
        provider_event_id: str,
        event_type: str,
        entity_id: str | None,
        raw_body: bytes,
        raw_payload: Mapping[str, Any],
        occurred_at: datetime | None,
        received_at: datetime,
        correlation_id: str,
        max_dispatch_attempts: int = 5,
    ) -> WebhookInsertResult:
        """Insert one verified inbox row and its durable dispatch atomically.

        The caller controls commit. A duplicate delivery returns the existing row and never
        creates another dispatch.
        """

        statement = (
            insert(WebhookEvent)
            .values(
                id=event_id,
                merchant_id=merchant_id,
                provider=provider,
                provider_event_id=provider_event_id,
                event_type=event_type,
                entity_id=entity_id,
                raw_body=raw_body,
                raw_payload=dict(raw_payload),
                raw_payload_sha256=sha256(raw_body).hexdigest(),
                signature_valid=True,
                signature_failure_code=None,
                ingestion_status="ACCEPTED",
                processing_state="PENDING",
                occurred_at=occurred_at,
                received_at=received_at,
                correlation_id=correlation_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    WebhookEvent.provider,
                    WebhookEvent.merchant_id,
                    WebhookEvent.provider_event_id,
                ],
                index_where=text("signature_valid"),
            )
            .returning(WebhookEvent)
        )
        inserted = (await self._session.scalars(statement)).one_or_none()
        if inserted is None:
            existing = (
                await self._session.scalars(
                    select(WebhookEvent).where(
                        WebhookEvent.provider == provider,
                        WebhookEvent.merchant_id == merchant_id,
                        WebhookEvent.provider_event_id == provider_event_id,
                        WebhookEvent.signature_valid.is_(True),
                    )
                )
            ).one()
            dispatch_id = await self._dispatch_id_for(existing.id)
            return WebhookInsertResult(existing, False, dispatch_id)

        dispatch_id = str(uuid4())
        self._session.add(
            EventDispatch(
                id=dispatch_id,
                merchant_id=merchant_id,
                webhook_event_id=event_id,
                queue_name="event_ingestion",
                state="PENDING",
                attempt_count=0,
                max_attempts=max_dispatch_attempts,
                available_at=received_at,
            )
        )
        await self._session.flush()
        return WebhookInsertResult(inserted, True, dispatch_id)

    async def record_invalid_webhook(
        self,
        *,
        event_id: str,
        merchant_id: str,
        provider: str,
        provider_event_id: str | None,
        raw_payload_sha256: str,
        received_at: datetime,
        correlation_id: str,
        failure_code: str,
    ) -> WebhookEvent:
        """Retain investigation metadata without storing or dispatching untrusted payload data."""

        event = WebhookEvent(
            id=event_id,
            merchant_id=merchant_id,
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=None,
            entity_id=None,
            raw_body=None,
            raw_payload=None,
            raw_payload_sha256=raw_payload_sha256,
            signature_valid=False,
            signature_failure_code=failure_code,
            ingestion_status="REJECTED_INVALID_SIGNATURE",
            processing_state="NOT_QUEUED",
            occurred_at=None,
            received_at=received_at,
            correlation_id=correlation_id,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def fetch_webhook_for_processing(
        self,
        *,
        merchant_id: str,
        webhook_event_id: str,
        for_update: bool = False,
    ) -> WebhookEvent | None:
        """Fetch only a verified, accepted event within its owning merchant scope."""

        statement = select(WebhookEvent).where(
            WebhookEvent.merchant_id == merchant_id,
            WebhookEvent.id == webhook_event_id,
            WebhookEvent.signature_valid.is_(True),
            WebhookEvent.ingestion_status == "ACCEPTED",
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def persist_normalized_event(
        self,
        *,
        event: Mapping[str, Any],
        webhook_event_id: str,
        correlations: Sequence[Mapping[str, str | None]] = (),
    ) -> NormalizedEvent:
        """Persist a contract-shaped normalized event once and attach typed correlations."""

        values = dict(event)
        values["id"] = values.pop("event_id")
        values["webhook_event_id"] = webhook_event_id
        values["occurred_at"] = _as_datetime(values["occurred_at"], "occurred_at")
        values["received_at"] = _as_datetime(values["received_at"], "received_at")
        values["normalized_payload"] = {key: _as_json_value(value) for key, value in event.items()}
        statement = (
            insert(NormalizedEvent)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(NormalizedEvent)
        )
        normalized = (await self._session.scalars(statement)).one_or_none()
        if normalized is None:
            normalized = (
                await self._session.scalars(
                    select(NormalizedEvent).where(
                        NormalizedEvent.merchant_id == event["merchant_id"],
                        NormalizedEvent.webhook_event_id == webhook_event_id,
                    )
                )
            ).one()
        else:
            for correlation in correlations:
                correlation_statement = (
                    insert(EventCorrelation)
                    .values(
                        merchant_id=normalized.merchant_id,
                        normalized_event_id=normalized.id,
                        reference_type=correlation["reference_type"],
                        external_id=correlation["external_id"],
                        internal_id=correlation.get("internal_id"),
                    )
                    .on_conflict_do_nothing()
                )
                await self._session.execute(correlation_statement)

        await self._session.execute(
            update(EventDispatch)
            .where(
                EventDispatch.merchant_id == normalized.merchant_id,
                EventDispatch.webhook_event_id == webhook_event_id,
            )
            .values(normalized_event_id=normalized.id)
        )
        return normalized

    async def claim_dispatches(
        self,
        *,
        now: datetime,
        lease_for: timedelta,
        limit: int,
    ) -> list[EventDispatch]:
        """Claim due rows using row locks; expired claims are safely recoverable."""

        claimable = or_(
            and_(
                EventDispatch.state.in_(("PENDING", "RETRY_SCHEDULED")),
                EventDispatch.available_at <= now,
            ),
            and_(
                EventDispatch.state.in_(("PROCESSING", "PUBLISHED")),
                EventDispatch.lease_expires_at.is_not(None),
                EventDispatch.lease_expires_at <= now,
            ),
        )
        statement: Select[tuple[EventDispatch]] = (
            select(EventDispatch)
            .where(claimable, EventDispatch.attempt_count < EventDispatch.max_attempts)
            .order_by(EventDispatch.available_at, EventDispatch.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        dispatches = list((await self._session.scalars(statement)).all())
        for dispatch in dispatches:
            dispatch.state = "PROCESSING"
            dispatch.attempt_count += 1
            dispatch.lease_token = uuid4().hex
            dispatch.lease_expires_at = now + lease_for
        await self._session.flush()
        return dispatches

    async def mark_dispatch_published(
        self,
        *,
        dispatch_id: str,
        lease_token: str,
        broker_task_id: str,
        published_at: datetime,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(EventDispatch)
                .where(
                    EventDispatch.id == dispatch_id,
                    EventDispatch.state == "PROCESSING",
                    EventDispatch.lease_token == lease_token,
                )
                .values(
                    state="PUBLISHED",
                    broker_task_id=broker_task_id,
                    published_at=published_at,
                )
            ),
        )
        return bool(result.rowcount)

    async def mark_dispatch_succeeded(
        self,
        *,
        dispatch_id: str,
        completed_at: datetime,
    ) -> bool:
        dispatch = await self._session.get(EventDispatch, dispatch_id, with_for_update=True)
        if dispatch is None or dispatch.state == "DEAD_LETTER":
            return False
        dispatch.state = "SUCCEEDED"
        dispatch.completed_at = completed_at
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        webhook = await self._session.get(WebhookEvent, dispatch.webhook_event_id)
        if webhook is not None:
            webhook.processing_state = "PROCESSED"
            webhook.processed_at = completed_at
        await self._session.flush()
        return True

    async def record_dispatch_failure(
        self,
        *,
        dispatch_id: str,
        now: datetime,
        retry_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> DispatchFailureResult:
        dispatch = await self._session.get(EventDispatch, dispatch_id, with_for_update=True)
        if dispatch is None:
            raise LookupError(f"Unknown dispatch {dispatch_id}")

        dispatch.last_error_code = error_code
        dispatch.last_error_detail = error_detail
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        webhook = await self._session.get(WebhookEvent, dispatch.webhook_event_id)
        if dispatch.attempt_count >= dispatch.max_attempts:
            dispatch.state = "DEAD_LETTER"
            dispatch.dead_lettered_at = now
            if webhook is not None:
                webhook.processing_state = "DEAD_LETTER"
                webhook.last_error_code = error_code
                webhook.last_error_detail = error_detail
        else:
            dispatch.state = "RETRY_SCHEDULED"
            dispatch.available_at = retry_at
            if webhook is not None:
                webhook.processing_state = "RETRY_SCHEDULED"
                webhook.last_error_code = error_code
                webhook.last_error_detail = error_detail
        await self._session.flush()
        return DispatchFailureResult(dispatch.id, dispatch.state, dispatch.attempt_count)

    async def requeue_dead_letter(
        self,
        *,
        dispatch_id: str,
        replayed_at: datetime,
        replayed_by: str,
    ) -> bool:
        """Explicitly requeue one dead letter while retaining replay metadata."""

        dispatch = await self._session.get(EventDispatch, dispatch_id, with_for_update=True)
        if dispatch is None or dispatch.state != "DEAD_LETTER":
            return False
        dispatch.state = "PENDING"
        dispatch.attempt_count = 0
        dispatch.replay_count += 1
        dispatch.available_at = replayed_at
        dispatch.lease_token = None
        dispatch.lease_expires_at = None
        dispatch.broker_task_id = None
        dispatch.last_replayed_at = replayed_at
        dispatch.last_replayed_by = replayed_by
        webhook = await self._session.get(WebhookEvent, dispatch.webhook_event_id)
        if webhook is not None:
            webhook.processing_state = "PENDING"
        await self._session.flush()
        return True

    async def _dispatch_id_for(self, webhook_event_id: str) -> str | None:
        return cast(
            str | None,
            await self._session.scalar(
                select(EventDispatch.id).where(EventDispatch.webhook_event_id == webhook_event_id)
            ),
        )

    async def _get_customer(self, merchant_id: str, customer_id: str) -> Customer:
        return (
            await self._session.scalars(
                select(Customer).where(
                    Customer.merchant_id == merchant_id, Customer.id == customer_id
                )
            )
        ).one()

    async def _get_payment(self, merchant_id: str, payment_id: str) -> Payment:
        return (
            await self._session.scalars(
                select(Payment).where(Payment.merchant_id == merchant_id, Payment.id == payment_id)
            )
        ).one()

    async def _get_subscription(self, merchant_id: str, subscription_id: str) -> Subscription:
        return (
            await self._session.scalars(
                select(Subscription).where(
                    Subscription.merchant_id == merchant_id,
                    Subscription.id == subscription_id,
                )
            )
        ).one()


def _provider_update_is_authoritative(
    *,
    current_status: Any,
    current_updated_at: Any,
    excluded_status: Any,
    excluded_updated_at: Any,
) -> Any:
    current_rank = case(
        PROVIDER_STATUS_PRECEDENCE,
        value=current_status,
        else_=0,
    )
    excluded_rank = case(
        PROVIDER_STATUS_PRECEDENCE,
        value=excluded_status,
        else_=0,
    )
    return or_(
        current_updated_at.is_(None),
        and_(
            excluded_updated_at.is_not(None),
            or_(
                excluded_updated_at > current_updated_at,
                and_(
                    excluded_updated_at == current_updated_at,
                    excluded_rank >= current_rank,
                ),
            ),
        ),
    )


def _as_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return parsed.astimezone(UTC)
    raise TypeError(f"{field_name} must be a datetime or ISO-8601 string")


def _as_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return value
