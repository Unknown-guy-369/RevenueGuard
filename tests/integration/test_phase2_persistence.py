"""PostgreSQL integration coverage for the Phase 2 persistence boundary."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from revenueguard_api.persistence import (
    DatabaseMerchantWebhookResolver,
    DatabaseWebhookIngestionService,
)
from revenueguard_api.webhooks import IngestionDisposition, VerifiedRazorpayWebhook
from revenueguard_integrations.persistence import (
    Base,
    EventDispatch,
    EventIngestionRepository,
    NormalizedEvent,
    WebhookEvent,
    create_session_factory,
)
from revenueguard_worker import tasks as worker_tasks
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Build every test in a unique schema and skip honestly without PostgreSQL."""

    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"phase2_test_{uuid4().hex}"
    async with administration_engine.begin() as connection:
        await connection.execute(CreateSchema(schema))

    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(sync_connection, checkfirst=False)
            )
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await administration_engine.dispose()


async def _create_merchant(
    factory: async_sessionmaker[AsyncSession], merchant_id: str = "merchant_001"
) -> None:
    async with factory.begin() as session:
        repository = EventIngestionRepository(session)
        await repository.upsert_merchant(
            merchant_id=merchant_id,
            display_name=f"Merchant {merchant_id}",
            provider_account_id=f"account_{merchant_id}",
        )


async def test_invalid_signature_cannot_poison_verified_deduplication(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _create_merchant(session_factory)
    received_at = datetime(2026, 8, 25, 4, 31, tzinfo=UTC)
    raw_body = b'{"event":"subscription.pending","payload":{"subscription":{}}}'

    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        rejected = await repository.record_invalid_webhook(
            event_id=str(uuid4()),
            merchant_id="merchant_001",
            provider="RAZORPAY",
            provider_event_id="rzp_event_001",
            raw_payload_sha256=sha256(raw_body).hexdigest(),
            received_at=received_at,
            correlation_id="corr_rejected_001",
            failure_code="SIGNATURE_MISMATCH",
        )
        assert rejected.raw_body is None
        assert rejected.raw_payload is None
        assert rejected.raw_payload_sha256 == sha256(raw_body).hexdigest()

        accepted = await repository.record_webhook(
            event_id=str(uuid4()),
            merchant_id="merchant_001",
            provider="RAZORPAY",
            provider_event_id="rzp_event_001",
            event_type="subscription.pending",
            entity_id="sub_001",
            raw_body=raw_body,
            raw_payload={"event": "subscription.pending", "payload": {"subscription": {}}},
            occurred_at=received_at,
            received_at=received_at,
            correlation_id="corr_001",
        )
        assert accepted.created is True
        assert accepted.dispatch_id is not None

    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        duplicate = await repository.record_webhook(
            event_id=str(uuid4()),
            merchant_id="merchant_001",
            provider="RAZORPAY",
            provider_event_id="rzp_event_001",
            event_type="subscription.pending",
            entity_id="sub_001",
            raw_body=raw_body,
            raw_payload={"event": "subscription.pending", "payload": {"subscription": {}}},
            occurred_at=received_at,
            received_at=received_at + timedelta(seconds=1),
            correlation_id="corr_duplicate_001",
        )
        assert duplicate.created is False
        assert duplicate.event.id == accepted.event.id
        assert duplicate.dispatch_id == accepted.dispatch_id

        processing_event = await repository.fetch_webhook_for_processing(
            merchant_id="merchant_001", webhook_event_id=accepted.event.id
        )
        assert processing_event is not None
        assert processing_event.raw_body == raw_body

        webhook_count = await session.scalar(select(func.count()).select_from(WebhookEvent))
        dispatch_count = await session.scalar(select(func.count()).select_from(EventDispatch))
        assert webhook_count == 2  # one redacted rejection and one accepted logical event
        assert dispatch_count == 1


async def test_normalization_dispatch_retry_and_dead_letter_are_durable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _create_merchant(session_factory)
    now = datetime(2026, 8, 25, 4, 31, tzinfo=UTC)
    raw_body = b'{"event":"subscription.pending"}'

    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        await repository.upsert_customer(
            merchant_id="merchant_001",
            customer_id="customer_001",
            provider_customer_id="cust_provider_001",
            provider_updated_at=now,
        )
        inbox = await repository.record_webhook(
            event_id=str(uuid4()),
            merchant_id="merchant_001",
            provider="RAZORPAY",
            provider_event_id="rzp_event_002",
            event_type="subscription.pending",
            entity_id="sub_002",
            raw_body=raw_body,
            raw_payload={"event": "subscription.pending"},
            occurred_at=now,
            received_at=now,
            correlation_id="corr_002",
            max_dispatch_attempts=2,
        )
        assert inbox.dispatch_id is not None

        normalized_payload = {
            "schema_version": "1.0",
            "event_id": "evt_internal_002",
            "merchant_id": "merchant_001",
            "source": "RAZORPAY",
            "source_event_id": "rzp_event_002",
            "event_type": "subscription.pending",
            "occurred_at": now,
            "received_at": now,
            "customer_id": "customer_001",
            "payment_id": None,
            "order_id": None,
            "subscription_id": None,
            "invoice_id": None,
            "payment_link_id": None,
            "amount_minor": 499900,
            "currency": "INR",
            "failure_code": "BAD_REQUEST_ERROR",
            "normalized_failure_category": "INSUFFICIENT_FUNDS",
            "correlation_id": "corr_002",
            "causation_id": None,
            "source_payload_reference": f"webhook_events/{inbox.event.id}",
        }
        first = await repository.persist_normalized_event(
            event=normalized_payload,
            webhook_event_id=inbox.event.id,
            correlations=(
                {
                    "reference_type": "CUSTOMER",
                    "external_id": "cust_provider_001",
                    "internal_id": "customer_001",
                },
            ),
        )
        second = await repository.persist_normalized_event(
            event=normalized_payload, webhook_event_id=inbox.event.id
        )
        assert first.id == second.id

    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        claimed = await repository.claim_dispatches(
            now=now, lease_for=timedelta(minutes=1), limit=10
        )
        assert len(claimed) == 1
        dispatch = claimed[0]
        assert dispatch.attempt_count == 1
        assert dispatch.lease_token is not None
        assert await repository.mark_dispatch_published(
            dispatch_id=dispatch.id,
            lease_token=dispatch.lease_token,
            broker_task_id="celery_task_001",
            published_at=now,
        )
        retry = await repository.record_dispatch_failure(
            dispatch_id=dispatch.id,
            now=now,
            retry_at=now + timedelta(minutes=5),
            error_code="NORMALIZATION_ERROR",
            error_detail="fixture-induced retry",
        )
        assert retry.state == "RETRY_SCHEDULED"

    retry_at = now + timedelta(minutes=5)
    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        claimed = await repository.claim_dispatches(
            now=retry_at, lease_for=timedelta(minutes=1), limit=10
        )
        assert len(claimed) == 1
        dead_letter = await repository.record_dispatch_failure(
            dispatch_id=claimed[0].id,
            now=retry_at,
            retry_at=retry_at + timedelta(minutes=10),
            error_code="NORMALIZATION_ERROR",
            error_detail="retry ceiling reached",
        )
        assert dead_letter.state == "DEAD_LETTER"
        assert dead_letter.attempt_count == 2

        assert await repository.requeue_dead_letter(
            dispatch_id=claimed[0].id,
            replayed_at=retry_at + timedelta(minutes=1),
            replayed_by="operator_test_001",
        )

    async with session_factory() as session:
        dispatch = (await session.scalars(select(EventDispatch))).one()
        webhook = (await session.scalars(select(WebhookEvent))).one()
        normalized_count = await session.scalar(select(func.count()).select_from(NormalizedEvent))
        assert dispatch.state == "PENDING"
        assert dispatch.dead_lettered_at == retry_at
        assert dispatch.replay_count == 1
        assert dispatch.last_replayed_by == "operator_test_001"
        assert webhook.processing_state == "PENDING"
        assert normalized_count == 1


async def test_provider_updates_do_not_regress_and_tenant_fks_are_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _create_merchant(session_factory, "merchant_a")
    await _create_merchant(session_factory, "merchant_b")
    older = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)
    newer = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)

    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        await repository.upsert_customer(
            merchant_id="merchant_a",
            customer_id="customer_shared_name",
            provider_customer_id="cust_a",
            provider_updated_at=older,
        )
        subscription = await repository.upsert_subscription(
            merchant_id="merchant_a",
            subscription_id="subscription_001",
            provider_subscription_id="sub_provider_001",
            customer_id="customer_shared_name",
            amount_minor=10000,
            currency="INR",
            status="CHARGED",
            provider_occurred_at=newer,
            provider_updated_at=newer,
        )
        assert subscription.status == "CHARGED"
        stale = await repository.upsert_subscription(
            merchant_id="merchant_a",
            subscription_id="subscription_001",
            provider_subscription_id="sub_provider_001",
            customer_id="customer_shared_name",
            amount_minor=10000,
            currency="INR",
            status="PENDING",
            provider_occurred_at=older,
            provider_updated_at=older,
        )
        assert stale.status == "CHARGED"
        assert stale.provider_updated_at == newer

    async with session_factory() as session:
        repository = EventIngestionRepository(session)
        with pytest.raises(IntegrityError):
            await repository.upsert_payment(
                merchant_id="merchant_b",
                payment_id="payment_cross_tenant",
                provider_payment_id="pay_cross_tenant",
                customer_id="customer_shared_name",
                order_id=None,
                amount_minor=10000,
                currency="INR",
                status="FAILED",
                provider_occurred_at=newer,
                provider_updated_at=newer,
            )
        await session.rollback()

    async with session_factory() as session:
        repository = EventIngestionRepository(session)
        with pytest.raises(IntegrityError):
            await repository.upsert_payment(
                merchant_id="merchant_a",
                payment_id="payment_negative",
                provider_payment_id="pay_negative",
                customer_id="customer_shared_name",
                order_id=None,
                amount_minor=-1,
                currency="INR",
                status="FAILED",
                provider_occurred_at=newer,
                provider_updated_at=newer,
            )
        await session.rollback()


async def test_api_inbox_to_worker_normalization_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merchant_id = "merchant_pipeline"
    await _create_merchant(session_factory, merchant_id)
    resolver = DatabaseMerchantWebhookResolver(
        session_factory,
        configured_merchant_id=merchant_id,
        webhook_secret="test-secret-not-a-live-credential",
    )
    assert await resolver.resolve(merchant_id) is not None
    assert await resolver.resolve("merchant_other") is None

    raw_body = (ROOT / "fixtures" / "razorpay" / "payment_failed.json").read_bytes()
    payload = json.loads(raw_body)
    assert isinstance(payload, dict)
    webhook = VerifiedRazorpayWebhook(
        merchant_id=merchant_id,
        provider_event_id="rzp_event_pipeline_001",
        raw_body=raw_body,
        payload=payload,
        received_at=datetime(2026, 8, 25, 4, 31, tzinfo=UTC),
    )
    ingestion = DatabaseWebhookIngestionService(session_factory, max_dispatch_attempts=3)

    results = [await ingestion.ingest_verified(webhook) for _ in range(5)]

    assert results == [IngestionDisposition.ACCEPTED] + [IngestionDisposition.DUPLICATE] * 4
    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        claimed = await repository.claim_dispatches(
            now=webhook.received_at,
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        assert len(claimed) == 1
        dispatch = claimed[0]

    monkeypatch.setattr(worker_tasks, "session_factory", session_factory)
    first = await worker_tasks._process_webhook_event(
        dispatch.id, merchant_id, dispatch.webhook_event_id
    )
    second = await worker_tasks._process_webhook_event(
        dispatch.id, merchant_id, dispatch.webhook_event_id
    )

    assert first["status"] == "processed"
    assert second["status"] == "already_processed"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(EventDispatch)) == 1
        assert await session.scalar(select(func.count()).select_from(NormalizedEvent)) == 1
