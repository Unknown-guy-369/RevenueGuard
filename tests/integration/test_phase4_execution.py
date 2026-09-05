"""PostgreSQL integration coverage for exactly-once action effects and verification."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from revenueguard_domain import (
    ActionStatus,
    ActionType,
    CaseState,
    EventSource,
    EvidenceSource,
    NormalizedFailureCategory,
    RevenueRiskEvent,
    conservative_default_policy,
)
from revenueguard_integrations.execution import (
    ActionExecutionService,
    PreparedExecution,
    ProviderExecutionResult,
    ProviderLookupResult,
)
from revenueguard_integrations.persistence import (
    ActionAttempt,
    ActionRepository,
    Base,
    EventIngestionRepository,
    RecoveryAction,
    RecoveryRepository,
    Subscription,
    VerifiedOutcome,
    create_session_factory,
)
from revenueguard_integrations.recovery import RecoveryApplicationService
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)


@pytest.fixture
async def phase4_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"phase4_test_{uuid4().hex}"
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


async def _seed_authorized_action(
    factory: async_sessionmaker[AsyncSession], *, merchant_id: str
) -> str:
    async with factory.begin() as session:
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_merchant(
            merchant_id=merchant_id,
            display_name=merchant_id,
            provider_account_id=f"account_{merchant_id}",
        )
        await RecoveryRepository(session).publish_policy(
            merchant_id=merchant_id,
            policy=conservative_default_policy(),
            published_by="TEST",
        )
        await ingestion.upsert_customer(
            merchant_id=merchant_id,
            customer_id="customer_001",
            provider_customer_id="customer_001",
            provider_updated_at=NOW,
        )
        await ingestion.upsert_payment(
            merchant_id=merchant_id,
            payment_id="payment_001",
            provider_payment_id="payment_001",
            customer_id="customer_001",
            order_id=None,
            amount_minor=10_000,
            currency="INR",
            status="FAILED",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        await ingestion.upsert_subscription(
            merchant_id=merchant_id,
            subscription_id="subscription_001",
            provider_subscription_id="subscription_001",
            customer_id="customer_001",
            amount_minor=10_000,
            currency="INR",
            status="PENDING",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        inbox = await ingestion.record_webhook(
            event_id=str(uuid4()),
            merchant_id=merchant_id,
            provider="RAZORPAY",
            provider_event_id=f"provider_{merchant_id}",
            event_type="payment.failed",
            entity_id="payment_001",
            raw_body=b"{}",
            raw_payload={},
            occurred_at=NOW,
            received_at=NOW,
            correlation_id=f"correlation_{merchant_id}",
        )
        normalized = await ingestion.persist_normalized_event(
            event={
                "schema_version": "1.0",
                "event_id": f"event_{merchant_id}",
                "merchant_id": merchant_id,
                "source": "RAZORPAY",
                "source_event_id": f"provider_{merchant_id}",
                "event_type": "payment.failed",
                "occurred_at": NOW,
                "received_at": NOW,
                "customer_id": "customer_001",
                "payment_id": "payment_001",
                "order_id": None,
                "subscription_id": "subscription_001",
                "invoice_id": None,
                "payment_link_id": None,
                "amount_minor": 10_000,
                "currency": "INR",
                "failure_code": "BAD_REQUEST_ERROR",
                "normalized_failure_category": "EXPIRED_PAYMENT_METHOD",
                "correlation_id": f"correlation_{merchant_id}",
                "causation_id": None,
                "source_payload_reference": f"webhook_events/{inbox.event.id}",
            },
            webhook_event_id=inbox.event.id,
        )
        ids = iter((f"case_{merchant_id}", f"receipt_{merchant_id}"))
        result = await RecoveryApplicationService(
            RecoveryRepository(session),
            action_repository=ActionRepository(session),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        ).process_event(merchant_id=merchant_id, normalized_event_id=normalized.id)
        assert result.case_state is CaseState.READY
        assert result.action_id is not None
        return result.action_id


async def _claim_and_prepare(
    factory: async_sessionmaker[AsyncSession], *, merchant_id: str, action_id: str
) -> str:
    async with factory.begin() as session:
        claims = await ActionRepository(session).claim_due_actions(
            now=NOW,
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        assert len(claims) == 1
        claim = claims[0]
    async with factory.begin() as session:
        prepared = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=claim.lease_token,
            started_at=NOW + timedelta(seconds=1),
        )
        assert isinstance(prepared, PreparedExecution)
        assert prepared.action.idempotency_key.startswith("rg:v1:")
    return claim.lease_token


async def test_api_ack_never_counts_money_and_signed_webhook_recovers_once(
    phase4_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_verified"
    action_id = await _seed_authorized_action(phase4_factory, merchant_id=merchant_id)
    lease_token = await _claim_and_prepare(
        phase4_factory, merchant_id=merchant_id, action_id=action_id
    )
    provider_object_id = "plink_test_verified_001"
    async with phase4_factory.begin() as session:
        disposition = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_execution_result(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            result=ProviderExecutionResult(
                status=ActionStatus.SUCCEEDED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=NOW + timedelta(seconds=2),
                response_category="API_ACCEPTED",
                provider_object_id=provider_object_id,
                response_reference="razorpay/payment_links/plink_test_verified_001",
                provider_status_code=200,
            ),
        )
        assert disposition.case_state is CaseState.VERIFYING
        assert await ActionRepository(session).recovered_totals(merchant_id=merchant_id) == ()

    paid_event = RevenueRiskEvent(
        event_id="event_paid_001",
        merchant_id=merchant_id,
        source=EventSource.RAZORPAY,
        source_event_id="provider_paid_001",
        event_type="payment_link.paid",
        occurred_at=NOW + timedelta(seconds=3),
        received_at=NOW + timedelta(seconds=4),
        customer_id="customer_001",
        payment_id="payment_paid_001",
        order_id=None,
        subscription_id=None,
        invoice_id=None,
        payment_link_id=provider_object_id,
        amount_minor=10_000,
        currency="INR",
        failure_code=None,
        normalized_failure_category=NormalizedFailureCategory.NONE,
        correlation_id="correlation_paid_001",
        causation_id=action_id,
        source_payload_reference="webhook_events/webhook_paid_001",
    )
    async with phase4_factory.begin() as session:
        service = ActionExecutionService(ActionRepository(session), RecoveryRepository(session))
        recovered = await service.verify_signed_event(
            event=paid_event,
            webhook_event_id="webhook_paid_001",
        )
        assert recovered is not None
        assert recovered.case_state is CaseState.RECOVERED
        duplicate = await service.verify_signed_event(
            event=paid_event,
            webhook_event_id="webhook_paid_001",
        )
        assert duplicate is not None
        assert duplicate.reason_code == "ALREADY_VERIFIED"
        totals = await ActionRepository(session).recovered_totals(merchant_id=merchant_id)
        assert totals[0].recovered_amount_minor == 10_000
        assert totals[0].verified_action_count == 1
        assert await session.scalar(select(func.count()).select_from(VerifiedOutcome)) == 2


async def test_worker_loss_after_call_start_becomes_unknown_and_suppresses_duplicate(
    phase4_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_crash_boundary"
    action_id = await _seed_authorized_action(phase4_factory, merchant_id=merchant_id)
    await _claim_and_prepare(phase4_factory, merchant_id=merchant_id, action_id=action_id)

    async with phase4_factory.begin() as session:
        marked = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).mark_stale_calls_unknown(now=NOW + timedelta(minutes=2), limit=10)
        assert len(marked) == 1
        assert marked[0].case_state is CaseState.UNKNOWN

    async with phase4_factory.begin() as session:
        repository = ActionRepository(session)
        assert (
            await repository.claim_due_actions(
                now=NOW + timedelta(days=1),
                lease_for=timedelta(minutes=1),
                limit=10,
            )
            == ()
        )
        row = await repository.get_action(merchant_id=merchant_id, action_id=action_id)
        assert row is not None
        assert row.status == ActionStatus.UNKNOWN.value
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 1
        assert await repository.recovered_totals(merchant_id=merchant_id) == ()

    async with phase4_factory.begin() as session:
        reconciled = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_lookup(
            merchant_id=merchant_id,
            action_id=action_id,
            result=ProviderLookupResult(
                status=ActionStatus.PENDING,
                evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                evidence_reference="razorpay/payment_links?reference_id=stable-key",
                observed_at=NOW + timedelta(minutes=3),
                is_authoritative=True,
                provider_object_id="plink_recovered_after_crash",
                reason_code="PAYMENT_LINK_CREATED",
            ),
        )
        assert reconciled.case_state is CaseState.VERIFYING
        assert await ActionRepository(session).recovered_totals(merchant_id=merchant_id) == ()

    async with phase4_factory.begin() as session:
        recovered = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_lookup(
            merchant_id=merchant_id,
            action_id=action_id,
            result=ProviderLookupResult(
                status=ActionStatus.SUCCEEDED,
                evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                evidence_reference="razorpay/payment_links/plink_recovered_after_crash",
                observed_at=NOW + timedelta(minutes=4),
                is_authoritative=True,
                provider_object_id="plink_recovered_after_crash",
                reason_code="PAYMENT_LINK_PAID",
            ),
        )
        assert recovered.case_state is CaseState.RECOVERED
        totals = await ActionRepository(session).recovered_totals(merchant_id=merchant_id)
        assert totals[0].recovered_amount_minor == 10_000


async def test_timeout_is_unknown_and_database_rejects_equivalent_active_action(
    phase4_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_timeout"
    action_id = await _seed_authorized_action(phase4_factory, merchant_id=merchant_id)
    lease_token = await _claim_and_prepare(
        phase4_factory, merchant_id=merchant_id, action_id=action_id
    )
    async with phase4_factory.begin() as session:
        disposition = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_execution_result(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            result=ProviderExecutionResult(
                status=ActionStatus.UNKNOWN,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=NOW + timedelta(seconds=2),
                response_category="TRANSPORT_TIMEOUT",
                error_code="PROVIDER_TIMEOUT",
            ),
        )
        assert disposition.case_state is CaseState.UNKNOWN
        assert await ActionRepository(session).has_unknown_equivalent(
            merchant_id=merchant_id,
            recovery_case_id=f"case_{merchant_id}",
            action_type=ActionType.CREATE_PAYMENT_LINK,
            target_id="subscription_001",
        )
        assert await ActionRepository(session).recovered_totals(merchant_id=merchant_id) == ()

    async with phase4_factory() as session:
        action = (
            await session.scalars(select(RecoveryAction).where(RecoveryAction.id == action_id))
        ).one()
        assert action.unknown_since == NOW + timedelta(seconds=2)


async def test_current_policy_and_provider_truth_are_rechecked_before_execution(
    phase4_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_paid_before_execution"
    action_id = await _seed_authorized_action(phase4_factory, merchant_id=merchant_id)
    async with phase4_factory.begin() as session:
        subscription = await session.get(Subscription, (merchant_id, "subscription_001"))
        assert subscription is not None
        subscription.status = "PAID"
        subscription.provider_updated_at = NOW + timedelta(milliseconds=500)

    async with phase4_factory.begin() as session:
        claims = await ActionRepository(session).claim_due_actions(
            now=NOW,
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        assert len(claims) == 1
    async with phase4_factory.begin() as session:
        result = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=claims[0].lease_token,
            started_at=NOW + timedelta(seconds=1),
        )
        assert not isinstance(result, PreparedExecution)
        assert result.case_state is CaseState.STOPPED
        assert result.reason_code == "PRE_EXECUTION_ALREADY_PAID"
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 0
        assert await ActionRepository(session).recovered_totals(merchant_id=merchant_id) == ()


async def test_retry_rechecks_provider_truth_before_starting_another_attempt(
    phase4_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_paid_before_retry"
    action_id = await _seed_authorized_action(phase4_factory, merchant_id=merchant_id)
    lease_token = await _claim_and_prepare(
        phase4_factory,
        merchant_id=merchant_id,
        action_id=action_id,
    )
    async with phase4_factory.begin() as session:
        first_result = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_execution_result(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            result=ProviderExecutionResult(
                status=ActionStatus.FAILED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=NOW + timedelta(seconds=2),
                response_category="RATE_LIMITED",
                provider_status_code=429,
                error_code="PROVIDER_RATE_LIMITED",
                retryable=True,
            ),
        )
        assert first_result.action_status is ActionStatus.PENDING
        subscription = await session.get(Subscription, (merchant_id, "subscription_001"))
        assert subscription is not None
        subscription.status = "PAID"
        subscription.provider_updated_at = NOW + timedelta(seconds=3)

    async with phase4_factory.begin() as session:
        claims = await ActionRepository(session).claim_due_actions(
            now=NOW + timedelta(seconds=10),
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        assert len(claims) == 1
        result = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=claims[0].lease_token,
            started_at=NOW + timedelta(seconds=11),
        )
        assert not isinstance(result, PreparedExecution)
        assert result.case_state is CaseState.STOPPED
        assert result.reason_code == "PRE_EXECUTION_ALREADY_PAID"
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 1


async def test_unverified_accepted_action_escalates_after_verification_deadline(
    phase4_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_verification_expired"
    action_id = await _seed_authorized_action(phase4_factory, merchant_id=merchant_id)
    lease_token = await _claim_and_prepare(
        phase4_factory,
        merchant_id=merchant_id,
        action_id=action_id,
    )
    async with phase4_factory.begin() as session:
        accepted = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_execution_result(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            result=ProviderExecutionResult(
                status=ActionStatus.SUCCEEDED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=NOW + timedelta(seconds=2),
                response_category="API_ACCEPTED",
                provider_object_id="plink_verification_expired",
                response_reference="razorpay/payment_links/plink_verification_expired",
                provider_status_code=200,
            ),
        )
        assert accepted.case_state is CaseState.VERIFYING

    async with phase4_factory.begin() as session:
        expired = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).record_lookup(
            merchant_id=merchant_id,
            action_id=action_id,
            result=ProviderLookupResult(
                status=ActionStatus.UNKNOWN,
                evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                evidence_reference="razorpay/payment_links/plink_verification_expired",
                observed_at=NOW + timedelta(hours=1, seconds=3),
                is_authoritative=False,
                provider_object_id="plink_verification_expired",
                reason_code="PROVIDER_STILL_PENDING",
            ),
        )
        assert expired.action_status is ActionStatus.UNKNOWN
        assert expired.case_state is CaseState.ESCALATED
        action = await ActionRepository(session).get_action(
            merchant_id=merchant_id,
            action_id=action_id,
        )
        assert action is not None
        assert action.dead_lettered_at == NOW + timedelta(hours=1, seconds=3)
        assert await ActionRepository(session).recovered_totals(merchant_id=merchant_id) == ()
