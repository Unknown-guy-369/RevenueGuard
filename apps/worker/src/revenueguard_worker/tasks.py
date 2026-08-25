"""Diagnostic and durable Phase 2 event-ingestion worker tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict, cast

from revenueguard_integrations.persistence import (
    EventDispatch,
    EventIngestionRepository,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from revenueguard_integrations.razorpay import (
    RazorpayEventError,
    normalize_razorpay_event,
)
from sqlalchemy import select

from revenueguard_worker.celery_app import celery_app
from revenueguard_worker.config import get_worker_settings

settings = get_worker_settings()
engine = create_database_engine(settings.database_url, use_null_pool=True)
session_factory = create_session_factory(engine)


class PingResult(TypedDict):
    status: Literal["ok"]
    service: str


class DispatchResult(TypedDict):
    claimed: int
    published: int
    rescheduled: int


class ProcessingResult(TypedDict):
    dispatch_id: str
    status: Literal["processed", "already_processed", "dead_letter", "retry_scheduled"]


@celery_app.task(name="revenueguard.system.ping")  # type: ignore[untyped-decorator]
def ping() -> PingResult:
    """Prove worker registration without touching financial state."""

    return {"status": "ok", "service": "revenueguard-worker"}


@celery_app.task(name="revenueguard.events.dispatch_pending")  # type: ignore[untyped-decorator]
def dispatch_pending_events() -> DispatchResult:
    """Publish due PostgreSQL dispatch rows and retain broker failures durably."""

    return asyncio.run(_dispatch_pending_events())


@celery_app.task(name="revenueguard.events.process")  # type: ignore[untyped-decorator]
def process_webhook_event(
    dispatch_id: str,
    merchant_id: str,
    webhook_event_id: str,
) -> ProcessingResult:
    """Normalize one accepted event idempotently; never perform a recovery action."""

    try:
        return asyncio.run(_process_webhook_event(dispatch_id, merchant_id, webhook_event_id))
    except RazorpayEventError as error:
        asyncio.run(
            _record_processing_failure(
                dispatch_id,
                error_code=error.code,
                error_detail=str(error)[:500],
                terminal=True,
            )
        )
        return {"dispatch_id": dispatch_id, "status": "dead_letter"}
    except Exception:
        state = asyncio.run(
            _record_processing_failure(
                dispatch_id,
                error_code="TRANSIENT_PROCESSING_ERROR",
                error_detail="transient worker processing failure",
                terminal=False,
            )
        )
        status: Literal["dead_letter", "retry_scheduled"] = (
            "dead_letter" if state == "DEAD_LETTER" else "retry_scheduled"
        )
        return {"dispatch_id": dispatch_id, "status": status}


async def _dispatch_pending_events() -> DispatchResult:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        claimed = await EventIngestionRepository(session).claim_dispatches(
            now=now,
            lease_for=timedelta(seconds=settings.event_dispatch_stale_after_seconds),
            limit=settings.event_dispatch_batch_size,
        )

    published = 0
    rescheduled = 0
    for dispatch in claimed:
        if dispatch.lease_token is None:
            continue
        task_id = f"event-dispatch-{dispatch.id}-attempt-{dispatch.attempt_count}"
        try:
            process_webhook_event.apply_async(
                args=[dispatch.id, dispatch.merchant_id, dispatch.webhook_event_id],
                task_id=task_id,
                queue=dispatch.queue_name,
            )
        except Exception:
            await _record_processing_failure(
                dispatch.id,
                error_code="BROKER_PUBLISH_FAILED",
                error_detail="broker publication failed",
                terminal=False,
            )
            rescheduled += 1
            continue

        async with session_scope(session_factory) as session:
            marked = await EventIngestionRepository(session).mark_dispatch_published(
                dispatch_id=dispatch.id,
                lease_token=dispatch.lease_token,
                broker_task_id=task_id,
                published_at=datetime.now(UTC),
            )
        if marked:
            published += 1

    return {"claimed": len(claimed), "published": published, "rescheduled": rescheduled}


async def _process_webhook_event(
    dispatch_id: str,
    merchant_id: str,
    webhook_event_id: str,
) -> ProcessingResult:
    async with session_scope(session_factory) as session:
        repository = EventIngestionRepository(session)
        dispatch = await session.scalar(
            select(EventDispatch).where(
                EventDispatch.id == dispatch_id,
                EventDispatch.merchant_id == merchant_id,
                EventDispatch.webhook_event_id == webhook_event_id,
            )
        )
        if dispatch is None:
            raise LookupError("tenant-scoped dispatch does not exist")
        if dispatch.state == "SUCCEEDED":
            return {"dispatch_id": dispatch_id, "status": "already_processed"}
        if dispatch.state == "DEAD_LETTER":
            return {"dispatch_id": dispatch_id, "status": "dead_letter"}

        webhook = await repository.fetch_webhook_for_processing(
            merchant_id=merchant_id,
            webhook_event_id=webhook_event_id,
            for_update=True,
        )
        if webhook is None or webhook.raw_body is None:
            raise LookupError("verified webhook body is unavailable")

        event = normalize_razorpay_event(
            webhook.raw_body,
            merchant_id=merchant_id,
            provider_event_id=cast(str, webhook.provider_event_id),
            event_id=f"evt_{webhook.id}",
            received_at=webhook.received_at,
            correlation_id=webhook.correlation_id,
            source_payload_reference=f"webhook_events/{webhook.provider_event_id}",
        )
        document = cast(Mapping[str, object], webhook.raw_payload or {})
        await _upsert_provider_entities(repository, event.to_dict(), document)
        await repository.persist_normalized_event(
            event=event.to_dict(),
            webhook_event_id=webhook_event_id,
            correlations=_event_correlations(event.to_dict()),
        )
        await repository.mark_dispatch_succeeded(
            dispatch_id=dispatch_id,
            completed_at=datetime.now(UTC),
        )
    return {"dispatch_id": dispatch_id, "status": "processed"}


async def _record_processing_failure(
    dispatch_id: str,
    *,
    error_code: str,
    error_detail: str,
    terminal: bool,
) -> str:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        repository = EventIngestionRepository(session)
        dispatch = await session.get(EventDispatch, dispatch_id, with_for_update=True)
        if dispatch is None:
            raise LookupError("dispatch does not exist")
        retry_delay_seconds = min(300, 5 * (2 ** max(dispatch.attempt_count - 1, 0)))
        if terminal:
            dispatch.attempt_count = dispatch.max_attempts
        result = await repository.record_dispatch_failure(
            dispatch_id=dispatch_id,
            now=now,
            retry_at=now + timedelta(seconds=retry_delay_seconds),
            error_code=error_code,
            error_detail=error_detail,
        )
    return result.state


async def _upsert_provider_entities(
    repository: EventIngestionRepository,
    event: Mapping[str, object],
    document: Mapping[str, object],
) -> None:
    merchant_id = cast(str, event["merchant_id"])
    occurred_at = _event_datetime(event["occurred_at"])
    customer_id = cast(str | None, event.get("customer_id"))
    if customer_id is not None:
        await repository.upsert_customer(
            merchant_id=merchant_id,
            customer_id=customer_id,
            provider_customer_id=customer_id,
            provider_updated_at=occurred_at,
        )

    payment_id = cast(str | None, event.get("payment_id"))
    payment = _provider_entity(document, "payment")
    if payment_id is not None:
        await repository.upsert_payment(
            merchant_id=merchant_id,
            payment_id=payment_id,
            provider_payment_id=payment_id,
            customer_id=customer_id,
            order_id=cast(str | None, event.get("order_id")),
            amount_minor=cast(int, event["amount_minor"]),
            currency=cast(str, event["currency"]),
            status=_entity_status(payment, cast(str, event["event_type"])),
            provider_occurred_at=occurred_at,
            provider_updated_at=_entity_updated_at(payment, occurred_at),
        )

    subscription_id = cast(str | None, event.get("subscription_id"))
    subscription = _provider_entity(document, "subscription")
    if subscription_id is not None:
        await repository.upsert_subscription(
            merchant_id=merchant_id,
            subscription_id=subscription_id,
            provider_subscription_id=subscription_id,
            customer_id=customer_id,
            amount_minor=cast(int, event["amount_minor"]),
            currency=cast(str, event["currency"]),
            status=_entity_status(subscription, cast(str, event["event_type"])),
            provider_occurred_at=occurred_at,
            provider_updated_at=_entity_updated_at(subscription, occurred_at),
        )


def _event_correlations(event: Mapping[str, object]) -> tuple[dict[str, str | None], ...]:
    field_types = (
        ("customer_id", "CUSTOMER"),
        ("payment_id", "PAYMENT"),
        ("order_id", "ORDER"),
        ("subscription_id", "SUBSCRIPTION"),
        ("invoice_id", "INVOICE"),
        ("payment_link_id", "PAYMENT_LINK"),
    )
    correlations: list[dict[str, str | None]] = []
    for field, reference_type in field_types:
        identifier = event.get(field)
        if isinstance(identifier, str):
            internal_id = (
                identifier if field in {"customer_id", "payment_id", "subscription_id"} else None
            )
            correlations.append(
                {
                    "reference_type": reference_type,
                    "external_id": identifier,
                    "internal_id": internal_id,
                }
            )
    return tuple(correlations)


def _provider_entity(
    document: Mapping[str, object], entity_type: str
) -> Mapping[str, object] | None:
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return None
    wrapper = payload.get(entity_type)
    if not isinstance(wrapper, dict):
        return None
    entity = wrapper.get("entity")
    return cast(Mapping[str, object], entity) if isinstance(entity, dict) else None


def _entity_status(entity: Mapping[str, object] | None, event_type: str) -> str:
    if entity is not None and isinstance(entity.get("status"), str):
        return cast(str, entity["status"]).upper()
    return event_type.rsplit(".", maxsplit=1)[-1].upper()


def _entity_updated_at(entity: Mapping[str, object] | None, fallback: datetime) -> datetime:
    if entity is None:
        return fallback
    timestamp = entity.get("created_at")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        return fallback
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except OverflowError, OSError, ValueError:
        return fallback


def _event_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("normalized event timestamp must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("normalized event timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
