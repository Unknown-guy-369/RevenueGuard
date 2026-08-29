"""PostgreSQL end-to-end coverage for the three Phase 6 playbooks."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from revenueguard_domain import (
    ActionStatus,
    CaseState,
    ContactChannel,
    DegradationPolicy,
    EvidenceSource,
    PaymentOutcomeObservation,
    conservative_default_policy,
)
from revenueguard_integrations.execution import (
    ActionExecutionService,
    ExecutionDisposition,
    PreparedExecution,
    ProviderExecutionResult,
    ProviderLookupResult,
)
from revenueguard_integrations.persistence import (
    ActionRepository,
    Base,
    CommunicationConsent,
    EventIngestionRepository,
    IncidentCaseLink,
    Invoice,
    PlaybookRepository,
    PortfolioIncident,
    PromiseToPay,
    ReceivableEscalation,
    RecoveryCase,
    RecoveryRepository,
    create_session_factory,
)
from revenueguard_integrations.persistence import (
    PaymentOutcomeObservation as PaymentOutcomeRow,
)
from revenueguard_integrations.playbooks import (
    BoundedPromiseExtractor,
    PaymentDegradationService,
    ReceivablesPlaybookService,
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
NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


class PromiseProvider:
    @property
    def model_version(self) -> str:
        return "phase6-promise-test-1"

    async def extract(self, *, text: str, max_output_tokens: int) -> Mapping[str, object]:
        del text, max_output_tokens
        return {
            "intent": "PROMISE_TO_PAY",
            "promised_date": "2026-08-31",
            "amount_minor": 8_000,
            "currency": "INR",
            "confidence_basis_points": 9_500,
        }


class DisputeProvider:
    @property
    def model_version(self) -> str:
        return "phase6-dispute-test-1"

    async def extract(self, *, text: str, max_output_tokens: int) -> Mapping[str, object]:
        del text, max_output_tokens
        return {"intent": "DISPUTE", "confidence_basis_points": 9_900}


@pytest.fixture
async def phase6_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"phase6_test_{uuid4().hex}"
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
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_merchant(
            merchant_id=merchant_id,
            display_name=merchant_id,
            provider_account_id=f"account_{merchant_id}",
        )
        await ingestion.upsert_customer(
            merchant_id=merchant_id,
            customer_id="customer_001",
            provider_customer_id="customer_001",
            provider_updated_at=NOW,
        )
        await RecoveryRepository(session).publish_policy(
            merchant_id=merchant_id,
            policy=conservative_default_policy(),
            published_by="TEST",
        )
        session.add(
            CommunicationConsent(
                merchant_id=merchant_id,
                customer_id="customer_001",
                channel=ContactChannel.EMAIL.value,
                state="GRANTED",
                opted_out=False,
                source="TEST",
                effective_at=NOW,
            )
        )


async def _ingest_invoice(
    factory: async_sessionmaker[AsyncSession],
    *,
    merchant_id: str,
    extractor: BoundedPromiseExtractor,
) -> None:
    async with factory.begin() as session:
        recovery = RecoveryRepository(session)
        result = await ReceivablesPlaybookService(
            PlaybookRepository(session),
            recovery,
            extractor=extractor,
            clock=lambda: NOW,
        ).ingest_overdue_invoice(
            merchant_id=merchant_id,
            source_event_id=f"invoice-overdue-{merchant_id}",
            correlation_id=f"correlation-{merchant_id}",
            invoice_id="invoice_001",
            customer_id="customer_001",
            amount_minor=10_000,
            outstanding_amount_minor=10_000,
            currency="INR",
            due_at=NOW - timedelta(days=2),
            occurred_at=NOW,
            received_at=NOW,
        )
        assert result.case_state is CaseState.READY


async def test_promise_survives_restart_and_blocks_stale_authorized_outreach(
    phase6_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_promise"
    extractor = BoundedPromiseExtractor(PromiseProvider())
    await _seed_merchant(phase6_factory, merchant_id)
    await _ingest_invoice(phase6_factory, merchant_id=merchant_id, extractor=extractor)

    async with phase6_factory.begin() as session:
        result = await ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            extractor=extractor,
            clock=lambda: NOW + timedelta(minutes=1),
        ).record_customer_response(
            merchant_id=merchant_id,
            source_response_id="response-promise-001",
            invoice_id="invoice_001",
            body="We will pay on the promised date.",
        )
        assert result.disposition == "PROMISE_SCHEDULED"

    # A new service/session simulates an API and worker restart: the reminder schedule is DB truth.
    async with phase6_factory.begin() as session:
        promise = (
            await session.scalars(
                select(PromiseToPay).where(PromiseToPay.merchant_id == merchant_id)
            )
        ).one()
        invoice = await session.get(Invoice, (merchant_id, "invoice_001"))
        assert promise.reminder_at == datetime(2026, 8, 30, tzinfo=UTC)
        assert promise.status == "ACTIVE"
        assert invoice is not None and invoice.status == "PROMISED"

        claims = await ActionRepository(session).claim_due_actions(
            now=NOW + timedelta(minutes=2),
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        assert len(claims) == 1
        disposition = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=claims[0].action_id,
            lease_token=claims[0].lease_token,
            started_at=NOW + timedelta(minutes=2),
        )
        assert isinstance(disposition, ExecutionDisposition)
        assert disposition.reason_code == "PRE_EXECUTION_ACTIVE_PROMISE_TO_PAY"
        assert disposition.case_state is CaseState.DECISION_PENDING

    async with phase6_factory.begin() as session:
        service = ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            extractor=extractor,
            clock=lambda: datetime(2026, 8, 30, tzinfo=UTC),
        )
        deferred = await service.schedule_due_promise_reminders(
            due_at=datetime(2026, 8, 30, tzinfo=UTC),
            limit=10,
        )
        assert deferred[0].disposition == "QUIET_HOURS"

    async with phase6_factory.begin() as session:
        service = ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            extractor=extractor,
            clock=lambda: datetime(2026, 8, 30, 7, tzinfo=UTC),
        )
        scheduled = await service.schedule_due_promise_reminders(
            due_at=datetime(2026, 8, 30, 7, tzinfo=UTC),
            limit=10,
        )
        promise = (
            await session.scalars(
                select(PromiseToPay).where(PromiseToPay.merchant_id == merchant_id)
            )
        ).one()
        assert scheduled[0].disposition == "REMINDER_AUTHORIZED"
        assert promise.reminder_action_id == scheduled[0].action_id

    async with phase6_factory.begin() as session:
        service = ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            extractor=extractor,
            clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
        )
        broken = await service.escalate_broken_promises(
            due_at=datetime(2026, 9, 1, tzinfo=UTC),
            limit=10,
        )
        invoice = await session.get(Invoice, (merchant_id, "invoice_001"))
        assert broken[0].disposition == "BROKEN_PROMISE_ESCALATED"
        assert invoice is not None and invoice.status == "ESCALATED"

        claims = await ActionRepository(session).claim_due_actions(
            now=datetime(2026, 9, 1, tzinfo=UTC),
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        assert len(claims) == 1
        disposition = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=claims[0].action_id,
            lease_token=claims[0].lease_token,
            started_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        assert isinstance(disposition, ExecutionDisposition)
        assert disposition.case_state is CaseState.STOPPED
        assert disposition.reason_code == "PRE_EXECUTION_SUBJECT_CANCELLED"


async def test_halted_subscription_is_recovered_as_a_failure_not_misclassified_cancelled(
    phase6_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_halted_subscription"
    await _seed_merchant(phase6_factory, merchant_id)
    async with phase6_factory.begin() as session:
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_payment(
            merchant_id=merchant_id,
            payment_id="payment_halted",
            provider_payment_id="payment_halted",
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
            subscription_id="subscription_halted",
            provider_subscription_id="subscription_halted",
            customer_id="customer_001",
            amount_minor=10_000,
            currency="INR",
            status="HALTED",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        webhook = await ingestion.record_webhook(
            event_id=str(uuid4()),
            merchant_id=merchant_id,
            provider="RAZORPAY",
            provider_event_id="subscription-halted-001",
            event_type="subscription.halted",
            entity_id="subscription_halted",
            raw_body=b"{}",
            raw_payload={},
            occurred_at=NOW,
            received_at=NOW,
            correlation_id="subscription-halted-correlation",
        )
        normalized = await ingestion.persist_normalized_event(
            event={
                "schema_version": "1.0",
                "event_id": "event_subscription_halted",
                "merchant_id": merchant_id,
                "source": "RAZORPAY",
                "source_event_id": "subscription-halted-001",
                "event_type": "subscription.halted",
                "occurred_at": NOW,
                "received_at": NOW,
                "customer_id": "customer_001",
                "payment_id": "payment_halted",
                "order_id": None,
                "subscription_id": "subscription_halted",
                "invoice_id": None,
                "payment_link_id": None,
                "amount_minor": 10_000,
                "currency": "INR",
                "failure_code": "issuer_down",
                "normalized_failure_category": "ISSUER_UNAVAILABLE",
                "correlation_id": "subscription-halted-correlation",
                "causation_id": None,
                "source_payload_reference": "webhook_events/subscription-halted-001",
            },
            webhook_event_id=webhook.event.id,
        )
        result = await RecoveryApplicationService(
            RecoveryRepository(session), clock=lambda: NOW
        ).process_event(merchant_id=merchant_id, normalized_event_id=normalized.id)

        assert result.case_state is CaseState.DEFERRED
        assert result.reason_code == "DIAGNOSIS_DELAY"


async def test_dispute_freezes_automation_and_creates_human_escalation(
    phase6_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_dispute"
    extractor = BoundedPromiseExtractor(DisputeProvider())
    await _seed_merchant(phase6_factory, merchant_id)
    await _ingest_invoice(phase6_factory, merchant_id=merchant_id, extractor=extractor)

    async with phase6_factory.begin() as session:
        result = await ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            extractor=extractor,
            clock=lambda: NOW + timedelta(minutes=1),
        ).record_customer_response(
            merchant_id=merchant_id,
            source_response_id="response-dispute-001",
            invoice_id="invoice_001",
            body="The invoice is disputed.",
        )
        invoice = await session.get(Invoice, (merchant_id, "invoice_001"))
        escalation = (
            await session.scalars(
                select(ReceivableEscalation).where(ReceivableEscalation.merchant_id == merchant_id)
            )
        ).one()
        assert result.disposition == "AUTOMATION_FROZEN_HUMAN_ESCALATION"
        assert invoice is not None and invoice.status == "DISPUTED"
        assert invoice.automation_frozen_at == NOW + timedelta(minutes=1)
        assert escalation.status == "OPEN"

        claims = await ActionRepository(session).claim_due_actions(
            now=NOW + timedelta(minutes=2),
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        disposition = await ActionExecutionService(
            ActionRepository(session), RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=claims[0].action_id,
            lease_token=claims[0].lease_token,
            started_at=NOW + timedelta(minutes=2),
        )
        assert isinstance(disposition, ExecutionDisposition)
        assert disposition.case_state is CaseState.STOPPED
        assert disposition.reason_code == "PRE_EXECUTION_PAYMENT_DISPUTED"


async def test_receivable_recovery_requires_authoritative_provider_evidence(
    phase6_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_receivable_verified"
    extractor = BoundedPromiseExtractor(PromiseProvider())
    await _seed_merchant(phase6_factory, merchant_id)
    await _ingest_invoice(phase6_factory, merchant_id=merchant_id, extractor=extractor)

    async with phase6_factory.begin() as session:
        actions = ActionRepository(session)
        claims = await actions.claim_due_actions(
            now=NOW,
            lease_for=timedelta(minutes=1),
            limit=10,
        )
        prepared = await ActionExecutionService(
            actions, RecoveryRepository(session)
        ).prepare_execution(
            merchant_id=merchant_id,
            action_id=claims[0].action_id,
            lease_token=claims[0].lease_token,
            started_at=NOW + timedelta(seconds=1),
        )
        assert isinstance(prepared, PreparedExecution)
        accepted = await ActionExecutionService(
            actions, RecoveryRepository(session)
        ).record_execution_result(
            merchant_id=merchant_id,
            action_id=claims[0].action_id,
            lease_token=claims[0].lease_token,
            result=ProviderExecutionResult(
                status=ActionStatus.SUCCEEDED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=NOW + timedelta(seconds=2),
                response_category="CONTACT_ACCEPTED",
                provider_object_id="contact_receivable_001",
                response_reference="contact-provider/contact_receivable_001",
            ),
        )
        assert accepted.case_state is CaseState.VERIFYING
        assert await actions.recovered_totals(merchant_id=merchant_id) == ()

    async with phase6_factory.begin() as session:
        actions = ActionRepository(session)
        verified = await ActionExecutionService(actions, RecoveryRepository(session)).record_lookup(
            merchant_id=merchant_id,
            action_id=claims[0].action_id,
            result=ProviderLookupResult(
                status=ActionStatus.SUCCEEDED,
                evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                evidence_reference="merchant-ledger/invoice_001/payment_001",
                observed_at=NOW + timedelta(hours=1),
                is_authoritative=True,
                provider_object_id="payment_receivable_001",
                reason_code="INVOICE_PAYMENT_VERIFIED",
            ),
        )
        totals = await actions.recovered_totals(merchant_id=merchant_id)
        assert verified.case_state is CaseState.RECOVERED
        assert totals[0].recovered_amount_minor == 10_000


async def test_degradation_incident_is_evidence_backed_and_resumes_gradually(
    phase6_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_degradation"
    await _seed_merchant(phase6_factory, merchant_id)
    await _seed_issuer_failure_case(phase6_factory, merchant_id)
    policy = DegradationPolicy()

    async with phase6_factory.begin() as session:
        repository = PlaybookRepository(session)
        observations = _window_observations(merchant_id=merchant_id, evaluated_at=NOW)
        for index, observation in enumerate(observations[:-1]):
            await repository.record_payment_observation(
                observation, source_event_id=f"outcome-{index}"
            )
        assessments = await PaymentDegradationService(
            repository, policy=policy
        ).observe_and_evaluate(observations[-1], source_event_id="outcome-final", evaluated_at=NOW)
        assert next(item for item in assessments if item.issuer_family == "BANK_A").degraded

        duplicate_assessments = await PaymentDegradationService(
            repository, policy=policy
        ).observe_and_evaluate(observations[-1], source_event_id="outcome-final", evaluated_at=NOW)
        assert next(
            item for item in duplicate_assessments if item.issuer_family == "BANK_A"
        ).degraded

        incident = (
            await session.scalars(
                select(PortfolioIncident).where(PortfolioIncident.merchant_id == merchant_id)
            )
        ).one()
        case = (
            await session.scalars(
                select(RecoveryCase).where(RecoveryCase.merchant_id == merchant_id)
            )
        ).one()
        assert incident.status == "ACTIVE"
        assert incident.current_failure_rate_basis_points == 8_000
        assert incident.threshold_version == policy.version
        assert incident.clear_window_count == 0
        assert case.active_incident_id == incident.id
        assert await session.scalar(select(func.count()).select_from(PortfolioIncident)) == 1

    for clear_index, evaluated_at in enumerate(
        (NOW + timedelta(minutes=16), NOW + timedelta(minutes=32)), start=1
    ):
        async with phase6_factory.begin() as session:
            repository = PlaybookRepository(session)
            for index in range(9):
                observation = _successful_observation(
                    merchant_id,
                    index=clear_index * 100 + index,
                    occurred_at=evaluated_at - timedelta(minutes=index),
                )
                await repository.record_payment_observation(
                    observation,
                    source_event_id=f"clear-{clear_index}-{index}",
                )
            final = _successful_observation(
                merchant_id,
                index=clear_index * 100 + 9,
                occurred_at=evaluated_at,
            )
            await PaymentDegradationService(repository, policy=policy).observe_and_evaluate(
                final,
                source_event_id=f"clear-{clear_index}-final",
                evaluated_at=evaluated_at,
            )

    async with phase6_factory() as session:
        incident = (
            await session.scalars(
                select(PortfolioIncident).where(PortfolioIncident.merchant_id == merchant_id)
            )
        ).one()
        link = (
            await session.scalars(
                select(IncidentCaseLink).where(IncidentCaseLink.merchant_id == merchant_id)
            )
        ).one()
        case = (
            await session.scalars(
                select(RecoveryCase).where(RecoveryCase.merchant_id == merchant_id)
            )
        ).one()
        assert incident.status == "RESOLVED"
        assert incident.resolution_reason == "FAILURE_RATE_RECOVERED"
        assert link.resume_after == NOW + timedelta(minutes=32)
        assert case.active_incident_id is None
        assert case.next_evaluation_at == link.resume_after
        assert await session.scalar(select(func.count()).select_from(PaymentOutcomeRow)) == 50


async def _seed_issuer_failure_case(
    factory: async_sessionmaker[AsyncSession], merchant_id: str
) -> None:
    async with factory.begin() as session:
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_payment(
            merchant_id=merchant_id,
            payment_id="payment_case",
            provider_payment_id="payment_case",
            customer_id="customer_001",
            order_id=None,
            amount_minor=10_000,
            currency="INR",
            status="FAILED",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        webhook = await ingestion.record_webhook(
            event_id=str(uuid4()),
            merchant_id=merchant_id,
            provider="RAZORPAY",
            provider_event_id="issuer-failure-case",
            event_type="payment.failed",
            entity_id="payment_case",
            raw_body=b"{}",
            raw_payload={},
            occurred_at=NOW,
            received_at=NOW,
            correlation_id="issuer-case-correlation",
        )
        normalized = await ingestion.persist_normalized_event(
            event={
                "schema_version": "1.0",
                "event_id": "event_issuer_case",
                "merchant_id": merchant_id,
                "source": "RAZORPAY",
                "source_event_id": "issuer-failure-case",
                "event_type": "payment.failed",
                "occurred_at": NOW,
                "received_at": NOW,
                "customer_id": "customer_001",
                "payment_id": "payment_case",
                "order_id": None,
                "subscription_id": None,
                "invoice_id": None,
                "payment_link_id": None,
                "amount_minor": 10_000,
                "currency": "INR",
                "failure_code": "issuer_down",
                "normalized_failure_category": "ISSUER_UNAVAILABLE",
                "correlation_id": "issuer-case-correlation",
                "causation_id": None,
                "source_payload_reference": "webhook_events/issuer-failure-case",
            },
            webhook_event_id=webhook.event.id,
        )
        result = await RecoveryApplicationService(
            RecoveryRepository(session), clock=lambda: NOW
        ).process_event(merchant_id=merchant_id, normalized_event_id=normalized.id)
        assert result.case_state is CaseState.DEFERRED


def _window_observations(
    *, merchant_id: str, evaluated_at: datetime
) -> tuple[PaymentOutcomeObservation, ...]:
    observations: list[PaymentOutcomeObservation] = []
    for index in range(20):
        observations.append(
            PaymentOutcomeObservation(
                observation_id=f"baseline-{index}",
                merchant_id=merchant_id,
                payment_id=f"baseline-payment-{index}",
                succeeded=index >= 2,
                payment_method="UPI",
                issuer_family="BANK_A",
                error_family="ISSUER_UNAVAILABLE",
                occurred_at=evaluated_at - timedelta(hours=1, minutes=index),
            )
        )
    for index in range(10):
        observations.append(
            PaymentOutcomeObservation(
                observation_id=f"current-{index}",
                merchant_id=merchant_id,
                payment_id=f"current-payment-{index}",
                succeeded=index >= 8,
                payment_method="UPI",
                issuer_family="BANK_A",
                error_family="ISSUER_UNAVAILABLE",
                occurred_at=evaluated_at - timedelta(minutes=index),
            )
        )
    return tuple(observations)


def _successful_observation(
    merchant_id: str, *, index: int, occurred_at: datetime
) -> PaymentOutcomeObservation:
    return PaymentOutcomeObservation(
        observation_id=f"clear-observation-{index}",
        merchant_id=merchant_id,
        payment_id=f"clear-payment-{index}",
        succeeded=True,
        payment_method="UPI",
        issuer_family="BANK_A",
        error_family="ISSUER_UNAVAILABLE",
        occurred_at=occurred_at,
    )
