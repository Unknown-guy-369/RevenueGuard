"""PostgreSQL coverage for Phase 3 recovery persistence and orchestration."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from revenueguard_domain import (
    ActionFingerprintInput,
    ActionType,
    CaseState,
    CaseTransition,
    HumanReviewDecision,
    HumanReviewRequest,
    MerchantPolicySnapshot,
    ReviewDecisionType,
    ReviewStatus,
    SubjectType,
    WorkflowType,
    conservative_default_policy,
)
from revenueguard_domain import (
    RecoveryCase as DomainRecoveryCase,
)
from revenueguard_integrations.persistence import (
    Base,
    DecisionReceipt,
    EventDispatch,
    EventIngestionRepository,
    HumanReview,
    NormalizedEvent,
    RecoveryCase,
    RecoveryCaseEvent,
    RecoveryRepository,
    StaleRecoveryCaseError,
    create_session_factory,
)
from revenueguard_integrations.persistence import (
    CaseTransition as CaseTransitionRow,
)
from revenueguard_integrations.persistence import (
    RecoveryAction as RecoveryActionRow,
)
from revenueguard_integrations.recovery import RecoveryApplicationService
from revenueguard_worker import tasks as worker_tasks
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def phase3_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"phase3_test_{uuid4().hex}"
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


async def _seed_merchant(factory: async_sessionmaker[AsyncSession], merchant_id: str) -> None:
    async with factory.begin() as session:
        await EventIngestionRepository(session).upsert_merchant(
            merchant_id=merchant_id,
            display_name=merchant_id,
            provider_account_id=f"account_{merchant_id}",
        )
        await RecoveryRepository(session).publish_policy(
            merchant_id=merchant_id,
            policy=conservative_default_policy(),
            published_by="TEST",
        )


async def _seed_failure_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    merchant_id: str,
    event_id: str = "event_failure_001",
    subscription_id: str | None = "subscription_001",
    payment_id: str = "payment_001",
    order_id: str | None = None,
    occurred_at: datetime = NOW,
    normalized_failure_category: str = "INSUFFICIENT_FUNDS",
) -> NormalizedEvent:
    async with factory.begin() as session:
        repository = EventIngestionRepository(session)
        await repository.upsert_customer(
            merchant_id=merchant_id,
            customer_id="customer_001",
            provider_customer_id="customer_001",
            provider_updated_at=occurred_at,
        )
        await repository.upsert_payment(
            merchant_id=merchant_id,
            payment_id=payment_id,
            provider_payment_id=payment_id,
            customer_id="customer_001",
            order_id=order_id,
            amount_minor=10_000,
            currency="INR",
            status="FAILED",
            provider_occurred_at=occurred_at,
            provider_updated_at=occurred_at,
        )
        if subscription_id is not None:
            await repository.upsert_subscription(
                merchant_id=merchant_id,
                subscription_id=subscription_id,
                provider_subscription_id=subscription_id,
                customer_id="customer_001",
                amount_minor=10_000,
                currency="INR",
                status="PENDING",
                provider_occurred_at=occurred_at,
                provider_updated_at=occurred_at,
            )
        inbox = await repository.record_webhook(
            event_id=str(uuid4()),
            merchant_id=merchant_id,
            provider="RAZORPAY",
            provider_event_id=f"provider_{event_id}",
            event_type="payment.failed",
            entity_id=payment_id,
            raw_body=b"{}",
            raw_payload={},
            occurred_at=occurred_at,
            received_at=occurred_at,
            correlation_id=f"correlation_{event_id}",
        )
        return await repository.persist_normalized_event(
            event={
                "schema_version": "1.0",
                "event_id": event_id,
                "merchant_id": merchant_id,
                "source": "RAZORPAY",
                "source_event_id": f"provider_{event_id}",
                "event_type": "payment.failed",
                "occurred_at": occurred_at,
                "received_at": occurred_at,
                "customer_id": "customer_001",
                "payment_id": payment_id,
                "order_id": order_id,
                "subscription_id": subscription_id,
                "invoice_id": None,
                "payment_link_id": None,
                "amount_minor": 10_000,
                "currency": "INR",
                "failure_code": "BAD_REQUEST_ERROR",
                "normalized_failure_category": normalized_failure_category,
                "correlation_id": f"correlation_{event_id}",
                "causation_id": None,
                "source_payload_reference": f"webhook_events/{inbox.event.id}",
            },
            webhook_event_id=inbox.event.id,
        )


def _domain_case(merchant_id: str, *, case_id: str = "case_001") -> DomainRecoveryCase:
    return DomainRecoveryCase(
        case_id=case_id,
        merchant_id=merchant_id,
        workflow_type=WorkflowType.FAILED_SUBSCRIPTION,
        subject_type=SubjectType.SUBSCRIPTION,
        subject_id="subscription_001",
        customer_id="customer_001",
        revenue_at_risk_minor=10_000,
        currency="INR",
        state=CaseState.DETECTED,
        state_version=1,
        retry_count=0,
        contact_count=0,
        created_at=NOW,
        updated_at=NOW,
    )


async def test_repository_is_tenant_scoped_converts_values_and_honours_row_locks(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_merchant(phase3_factory, "merchant_a")
    await _seed_merchant(phase3_factory, "merchant_b")
    event = await _seed_failure_event(phase3_factory, merchant_id="merchant_a")
    async with phase3_factory.begin() as session:
        repository = RecoveryRepository(session)
        await repository.create_case(
            replace(_domain_case("merchant_a"), diagnosis_confidence=0.875),
            recovery_episode_key="a" * 64,
            latest_evidence_event_id=event.id,
            latest_evidence_occurred_at=event.occurred_at,
            diagnosis_confidence_basis_points=8750,
        )

    async with phase3_factory() as locking_session:
        async with locking_session.begin():
            locked = await RecoveryRepository(locking_session).get_case(
                merchant_id="merchant_a", case_id="case_001", for_update=True
            )
            assert locked is not None
            assert locked.diagnosis_confidence == 0.875
            assert (
                await RecoveryRepository(locking_session).get_case(
                    merchant_id="merchant_b", case_id="case_001"
                )
                is None
            )
            async with phase3_factory() as competing_session:
                async with competing_session.begin():
                    await competing_session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError):
                        await RecoveryRepository(competing_session).get_case(
                            merchant_id="merchant_a",
                            case_id="case_001",
                            for_update=True,
                        )


async def test_provider_authority_is_subscription_first_and_subject_scoped(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_merchant(phase3_factory, "merchant_authority")
    event = await _seed_failure_event(
        phase3_factory,
        merchant_id="merchant_authority",
    )
    async with phase3_factory() as session:
        repository = RecoveryRepository(session)
        event_facts = await repository.authoritative_facts(event)
        assert event_facts.status == "PENDING"
        subscription_facts = await repository.authoritative_facts_for_case(
            merchant_id="merchant_authority",
            case=_domain_case("merchant_authority"),
        )
        assert subscription_facts.status == "PENDING"
        payment_case = replace(
            _domain_case("merchant_authority"),
            subject_type=SubjectType.PAYMENT,
            subject_id="payment_001",
        )
        payment_facts = await repository.authoritative_facts_for_case(
            merchant_id="merchant_authority",
            case=payment_case,
        )
        assert payment_facts.status == "FAILED"
        invoice_facts = await repository.authoritative_facts_for_case(
            merchant_id="merchant_authority",
            case=replace(
                _domain_case("merchant_authority"),
                subject_type=SubjectType.INVOICE,
                subject_id="invoice_001",
            ),
        )
        assert invoice_facts.status is None
        with pytest.raises(ValueError, match="does not belong"):
            await repository.authoritative_facts_for_case(
                merchant_id="merchant_other",
                case=_domain_case("merchant_authority"),
            )


async def test_policy_review_conversion_digest_and_optimistic_transition(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_policy"
    await _seed_merchant(phase3_factory, merchant_id)
    event = await _seed_failure_event(phase3_factory, merchant_id=merchant_id)
    case = _domain_case(merchant_id)
    policy = conservative_default_policy()
    async with phase3_factory.begin() as session:
        repository = RecoveryRepository(session)
        await repository.create_case(
            case,
            recovery_episode_key="b" * 64,
            latest_evidence_event_id=event.id,
            latest_evidence_occurred_at=event.occurred_at,
        )
        effective = await repository.effective_policy(
            merchant_id=merchant_id,
            evaluated_at=NOW,
        )
        assert effective.content_digest == policy.content_digest
        candidate_fingerprint = ActionFingerprintInput(
            case_id=case.case_id,
            action_type=ActionType.CREATE_PAYMENT_LINK.value,
            target=case.subject_id,
            amount_minor=case.revenue_at_risk_minor,
            currency=case.currency,
            logical_attempt=1,
            policy_digest=policy.content_digest,
        ).digest()
        review = HumanReviewRequest(
            review_id="review_001",
            merchant_id=merchant_id,
            case_id=case.case_id,
            action_fingerprint=candidate_fingerprint,
            proposed_action_type=ActionType.CREATE_PAYMENT_LINK.value,
            evidence_references=(event.id,),
            policy_version=policy.version,
            policy_digest=policy.content_digest,
            reason_code="HUMAN_REVIEW_REQUIRED",
            risk_detail="test",
            requested_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            status=ReviewStatus.REQUESTED,
        )
        await repository.store_review(review)
        loaded = await repository.get_review(
            merchant_id=merchant_id,
            review_id=review.review_id,
            for_update=True,
        )
        assert loaded == review

        transition = CaseTransition(
            case_id=case.case_id,
            merchant_id=merchant_id,
            before_state=CaseState.DETECTED,
            after_state=CaseState.DIAGNOSING,
            before_version=1,
            after_version=2,
            actor="TEST",
            reason_code="DIAGNOSIS_STARTED",
            reason_detail=None,
            correlation_id="correlation_001",
            policy_version=policy.version,
            authoritative_evidence_reference=None,
            occurred_at=NOW,
        )
        await repository.apply_transition(
            updated_case=replace(
                case,
                state=CaseState.DIAGNOSING,
                state_version=2,
            ),
            transition=transition,
        )
        with pytest.raises(StaleRecoveryCaseError):
            await repository.apply_transition(
                updated_case=replace(
                    case,
                    state=CaseState.DIAGNOSING,
                    state_version=2,
                ),
                transition=transition,
            )


async def test_recovery_service_is_atomic_and_replay_idempotent_when_policy_defers(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_service"
    await _seed_merchant(phase3_factory, merchant_id)
    event = await _seed_failure_event(phase3_factory, merchant_id=merchant_id)
    ids = iter(("case_service", "receipt_service"))
    async with phase3_factory.begin() as session:
        service = RecoveryApplicationService(
            RecoveryRepository(session),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        )
        first = await service.process_event(
            merchant_id=merchant_id,
            normalized_event_id=event.id,
        )
        second = await service.process_event(
            merchant_id=merchant_id,
            normalized_event_id=event.id,
        )
        assert first.case_state is CaseState.DEFERRED
        assert first.receipt_id == "receipt_service"
        assert second.reason_code == "EVENT_ALREADY_LINKED"

    async with phase3_factory() as session:
        assert await session.scalar(select(func.count()).select_from(RecoveryCase)) == 1
        assert await session.scalar(select(func.count()).select_from(RecoveryCaseEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(DecisionReceipt)) == 1
        assert await session.scalar(select(func.count()).select_from(CaseTransitionRow)) == 4
        assert await session.scalar(select(func.count()).select_from(HumanReview)) == 0
        case = (await session.scalars(select(RecoveryCase))).one()
        assert case.state == CaseState.DEFERRED.value
        assert case.state != CaseState.EXECUTING.value
        assert await session.scalar(select(func.count()).select_from(RecoveryActionRow)) == 0


async def test_payment_attempts_for_one_order_share_one_deferred_recovery_case(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_order_coordination"
    await _seed_merchant(phase3_factory, merchant_id)
    first_event = await _seed_failure_event(
        phase3_factory,
        merchant_id=merchant_id,
        event_id="event_order_attempt_001",
        subscription_id=None,
        payment_id="payment_attempt_001",
        order_id="order_shared_001",
        normalized_failure_category="UNKNOWN",
    )
    second_event = await _seed_failure_event(
        phase3_factory,
        merchant_id=merchant_id,
        event_id="event_order_attempt_002",
        subscription_id=None,
        payment_id="payment_attempt_002",
        order_id="order_shared_001",
        occurred_at=NOW + timedelta(seconds=1),
        normalized_failure_category="UNKNOWN",
    )
    ids = iter(("case_order", "receipt_order_001", "receipt_order_002"))
    async with phase3_factory.begin() as session:
        service = RecoveryApplicationService(
            RecoveryRepository(session),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        )
        first = await service.process_event(
            merchant_id=merchant_id,
            normalized_event_id=first_event.id,
        )
        second = await service.process_event(
            merchant_id=merchant_id,
            normalized_event_id=second_event.id,
        )

        assert first.case_id == "case_order"
        assert second.case_id == "case_order"
        assert first.case_state is CaseState.DEFERRED
        assert second.case_state is CaseState.DEFERRED
        assert first.review_id is None
        assert second.review_id is None
        assert first.action_id is None
        assert second.action_id is None

    async with phase3_factory() as session:
        assert await session.scalar(select(func.count()).select_from(RecoveryCase)) == 1
        assert await session.scalar(select(func.count()).select_from(RecoveryCaseEvent)) == 2
        assert await session.scalar(select(func.count()).select_from(DecisionReceipt)) == 2
        assert await session.scalar(select(func.count()).select_from(HumanReview)) == 0
        assert await session.scalar(select(func.count()).select_from(RecoveryActionRow)) == 0
        case = (await session.scalars(select(RecoveryCase))).one()
        assert case.retry_count == 2
        assert case.next_evaluation_at == NOW + timedelta(minutes=15, seconds=1)


async def test_bounded_unknown_retries_escalate_to_review_without_an_executable_action(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_bounded_escalation"
    await _seed_merchant(phase3_factory, merchant_id)
    policy = replace(
        conservative_default_policy(),
        version="bounded-escalation-test",
        effective_at=NOW - timedelta(hours=1),
        retry_limit=0,
    )
    async with phase3_factory.begin() as session:
        await RecoveryRepository(session).publish_policy(
            merchant_id=merchant_id,
            policy=policy,
            published_by="TEST",
        )
    event = await _seed_failure_event(
        phase3_factory,
        merchant_id=merchant_id,
        subscription_id=None,
        normalized_failure_category="UNKNOWN",
    )
    ids = iter(("case_bounded", "review_bounded", "receipt_bounded"))
    async with phase3_factory.begin() as session:
        result = await RecoveryApplicationService(
            RecoveryRepository(session),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        ).process_event(
            merchant_id=merchant_id,
            normalized_event_id=event.id,
        )

        assert result.case_state is CaseState.ESCALATED
        assert result.review_id == "review_bounded"
        assert result.action_id is None
        assert result.reason_code == "AGENT_ESCALATION_REQUESTED"

    async with phase3_factory() as session:
        review = (await session.scalars(select(HumanReview))).one()
        assert review.proposed_action_type == ActionType.ESCALATE_HUMAN.value
        assert review.reason_code == "AGENT_ESCALATION_REQUESTED"
        assert await session.scalar(select(func.count()).select_from(RecoveryActionRow)) == 0


async def test_deferred_case_is_re_evaluated_against_current_policy_and_provider_truth(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_deferred"
    await _seed_merchant(phase3_factory, merchant_id)
    event = await _seed_failure_event(phase3_factory, merchant_id=merchant_id)
    ids = iter(("case_deferred", "receipt_initial", "receipt_resumed"))
    async with phase3_factory.begin() as session:
        service = RecoveryApplicationService(
            RecoveryRepository(session),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        )
        initial = await service.process_event(
            merchant_id=merchant_id,
            normalized_event_id=event.id,
        )
        assert initial.case_state is CaseState.DEFERRED

    async with phase3_factory.begin() as session:
        resumed = await RecoveryApplicationService(
            RecoveryRepository(session),
            id_generator=lambda _prefix: next(ids),
        ).reevaluate_deferred(
            due_at=NOW + timedelta(days=1, seconds=1),
            limit=10,
        )
        assert len(resumed) == 1
        assert resumed[0].case_state is CaseState.READY
        assert resumed[0].receipt_id == "receipt_resumed"


async def test_human_approval_is_action_bound_and_rechecked_before_ready(
    phase3_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_review"
    await _seed_merchant(phase3_factory, merchant_id)
    strict_policy = replace(
        conservative_default_policy(),
        version="phase3-human-review-test",
        effective_at=NOW - timedelta(hours=1),
        human_review_amount_minor=1,
    )
    async with phase3_factory.begin() as session:
        await RecoveryRepository(session).publish_policy(
            merchant_id=merchant_id,
            policy=strict_policy,
            published_by="TEST",
        )
    event = await _seed_failure_event(phase3_factory, merchant_id=merchant_id)
    async with phase3_factory.begin() as session:
        persisted = await session.get(NormalizedEvent, event.id)
        assert persisted is not None
        persisted.normalized_failure_category = "EXPIRED_PAYMENT_METHOD"

    ids = iter(("case_review", "review_required", "receipt_review", "receipt_approved"))
    async with phase3_factory.begin() as session:
        service = RecoveryApplicationService(
            RecoveryRepository(session),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        )
        escalated = await service.process_event(
            merchant_id=merchant_id,
            normalized_event_id=event.id,
        )
        assert escalated.case_state is CaseState.ESCALATED
        assert escalated.review_id == "review_required"

    async with phase3_factory.begin() as session:
        approved = await RecoveryApplicationService(
            RecoveryRepository(session),
            clock=lambda: NOW + timedelta(minutes=1),
            id_generator=lambda _prefix: next(ids),
        ).decide_review(
            merchant_id=merchant_id,
            decision=HumanReviewDecision(
                review_id="review_required",
                decision=ReviewDecisionType.APPROVE,
                reviewer_id="operator_001",
                rationale="verified merchant approval",
                decided_at=NOW + timedelta(minutes=1),
            ),
        )
        assert approved.case_state is CaseState.READY
        assert approved.receipt_id == "receipt_approved"

    async with phase3_factory() as session:
        review = (await session.scalars(select(HumanReview))).one()
        assert review.status == ReviewStatus.APPROVED.value
        assert await session.scalar(select(func.count()).select_from(DecisionReceipt)) == 2


async def test_worker_crash_rolls_back_recovery_and_retry_creates_one_effect(
    phase3_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merchant_id = "merchant_crash"
    await _seed_merchant(phase3_factory, merchant_id)
    raw_body = (ROOT / "fixtures" / "razorpay" / "payment_failed.json").read_bytes()
    async with phase3_factory.begin() as session:
        repository = EventIngestionRepository(session)
        inbox = await repository.record_webhook(
            event_id=str(uuid4()),
            merchant_id=merchant_id,
            provider="RAZORPAY",
            provider_event_id="provider_crash_001",
            event_type="payment.failed",
            entity_id="payment_crash_001",
            raw_body=raw_body,
            raw_payload={},
            occurred_at=NOW,
            received_at=NOW,
            correlation_id="correlation_crash_001",
        )
        claimed = await repository.claim_dispatches(
            now=NOW,
            lease_for=timedelta(minutes=1),
            limit=1,
        )
        assert len(claimed) == 1
        dispatch = claimed[0]

    monkeypatch.setattr(worker_tasks, "session_factory", phase3_factory)
    original_process_event = RecoveryApplicationService.process_event

    async def crash_after_normalization(
        self: RecoveryApplicationService, *, merchant_id: str, normalized_event_id: str
    ) -> object:
        del self, merchant_id, normalized_event_id
        raise RuntimeError("simulated crash boundary")

    monkeypatch.setattr(
        RecoveryApplicationService,
        "process_event",
        crash_after_normalization,
    )
    with pytest.raises(RuntimeError, match="crash boundary"):
        await worker_tasks._process_webhook_event(
            dispatch.id,
            merchant_id,
            inbox.event.id,
        )
    async with phase3_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NormalizedEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(RecoveryCase)) == 0
        persisted_dispatch = await session.get(EventDispatch, dispatch.id)
        assert persisted_dispatch is not None
        assert persisted_dispatch.state == "PROCESSING"

    monkeypatch.setattr(RecoveryApplicationService, "process_event", original_process_event)
    first = await worker_tasks._process_webhook_event(
        dispatch.id,
        merchant_id,
        inbox.event.id,
    )
    second = await worker_tasks._process_webhook_event(
        dispatch.id,
        merchant_id,
        inbox.event.id,
    )
    assert first["status"] == "processed"
    assert second["status"] == "already_processed"
    async with phase3_factory() as session:
        assert await session.scalar(select(func.count()).select_from(NormalizedEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(RecoveryCase)) == 1
        assert await session.scalar(select(func.count()).select_from(DecisionReceipt)) == 1


def test_model_policy_snapshot_keeps_money_currency_explicit() -> None:
    policy = MerchantPolicySnapshot(
        version="test",
        effective_at=NOW,
        allowed_actions=frozenset({ActionType.NO_ACTION}),
        retry_limit=0,
        contact_limit=0,
        minimum_expected_net_recovery_minor=0,
        human_review_amount_minor=0,
        minimum_confidence_basis_points=0,
        default_defer_seconds=60,
        timezone="UTC",
        quiet_hours_start=time(0),
        quiet_hours_end=time(0),
        currency="INR",
    )
    assert policy.canonical_document()["currency"] == "INR"
