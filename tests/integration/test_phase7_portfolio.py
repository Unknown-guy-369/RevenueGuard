"""PostgreSQL integration coverage for Phase 7 portfolio coordination."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import revenueguard_integrations.recovery.service as recovery_service_module
from revenueguard_domain import (
    CaseState,
    ContactChannel,
    PaymentOutcomeObservation,
    conservative_default_policy,
)
from revenueguard_integrations.persistence import (
    Base,
    CommunicationConsent,
    Customer,
    CustomerIntervention,
    DecisionReceipt,
    EventIngestionRepository,
    IncidentCaseLink,
    Merchant,
    PlaybookRepository,
    PortfolioIncident,
    RecoveryAction,
    RecoveryCase,
    RecoveryRepository,
    create_session_factory,
)
from revenueguard_integrations.playbooks import (
    PaymentDegradationService,
    ReceivablesPlaybookService,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


@pytest.fixture
async def phase7_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"phase7_test_{uuid4().hex}"
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


async def test_customer_contact_snapshot_aggregates_all_customer_cases(
    phase7_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with phase7_factory.begin() as session:
        session.add(
            Merchant(
                id="merchant_phase7",
                display_name="Phase 7 Merchant",
                provider="RAZORPAY",
                provider_account_id="account_phase7",
                status="ACTIVE",
            )
        )
        session.add(
            Merchant(
                id="merchant_phase7_other",
                display_name="Other Phase 7 Merchant",
                provider="RAZORPAY",
                provider_account_id="account_phase7_other",
                status="ACTIVE",
            )
        )
        await session.flush()
        session.add(
            Customer(
                merchant_id="merchant_phase7",
                id="customer_shared",
                provider_customer_id="customer_shared",
                provider_updated_at=NOW,
            )
        )
        session.add(
            Customer(
                merchant_id="merchant_phase7_other",
                id="customer_shared",
                provider_customer_id="customer_shared",
                provider_updated_at=NOW,
            )
        )
        await session.flush()
        for index, contact_count in enumerate((1, 2), start=1):
            session.add(
                RecoveryCase(
                    merchant_id="merchant_phase7",
                    id=f"case_{index}",
                    schema_version="1.0",
                    workflow_type=("FAILED_SUBSCRIPTION" if index == 1 else "B2B_PROMISE_TO_PAY"),
                    subject_type="SUBSCRIPTION" if index == 1 else "INVOICE",
                    subject_id=f"subject_{index}",
                    customer_id="customer_shared",
                    revenue_at_risk_minor=10_000,
                    currency="INR",
                    state="DECISION_PENDING",
                    state_version=1,
                    retry_count=0,
                    contact_count=contact_count,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

        session.add(
            RecoveryCase(
                merchant_id="merchant_phase7_other",
                id="case_other_merchant",
                schema_version="1.0",
                workflow_type="FAILED_SUBSCRIPTION",
                subject_type="SUBSCRIPTION",
                subject_id="subject_other",
                customer_id="customer_shared",
                revenue_at_risk_minor=10_000,
                currency="INR",
                state="DECISION_PENDING",
                state_version=1,
                retry_count=0,
                contact_count=9,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    async with phase7_factory.begin() as session:
        snapshot = await RecoveryRepository(session).customer_contact_snapshot(
            merchant_id="merchant_phase7",
            customer_id="customer_shared",
            for_update=True,
        )

        assert snapshot.aggregate_contact_count == 3
        assert snapshot.active_case_ids == ("case_1", "case_2")
        assert snapshot.active_intervention_id is None

        other_snapshot = await RecoveryRepository(session).customer_contact_snapshot(
            merchant_id="merchant_phase7_other",
            customer_id="customer_shared",
        )
        assert other_snapshot.aggregate_contact_count == 9
        assert other_snapshot.active_case_ids == ("case_other_merchant",)


async def test_multiple_playbooks_create_one_coordinated_customer_intervention(
    phase7_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_coordinated"
    async with phase7_factory.begin() as session:
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_merchant(
            merchant_id=merchant_id,
            display_name="Coordinated Merchant",
            provider_account_id="account_coordinated",
        )
        await ingestion.upsert_customer(
            merchant_id=merchant_id,
            customer_id="customer_shared",
            provider_customer_id="customer_shared",
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
                customer_id="customer_shared",
                channel=ContactChannel.EMAIL.value,
                state="GRANTED",
                opted_out=False,
                source="TEST",
                effective_at=NOW,
            )
        )

    async def ingest_invoice(index: int):
        async with phase7_factory.begin() as session:
            return await ReceivablesPlaybookService(
                PlaybookRepository(session),
                RecoveryRepository(session),
                clock=lambda: NOW,
            ).ingest_overdue_invoice(
                merchant_id=merchant_id,
                source_event_id=f"invoice-overdue-{index}",
                correlation_id=f"correlation-{index}",
                invoice_id=f"invoice_{index}",
                customer_id="customer_shared",
                amount_minor=10_000,
                outstanding_amount_minor=10_000,
                currency="INR",
                due_at=NOW - timedelta(days=index),
                occurred_at=NOW,
                received_at=NOW,
            )

    results = await asyncio.gather(*(ingest_invoice(index) for index in (1, 2)))
    ready = next(result for result in results if result.case_state is CaseState.READY)
    deferred = next(result for result in results if result.case_state is CaseState.DEFERRED)
    assert deferred.reason_code == "CUSTOMER_CONTACT_ALREADY_IN_PROGRESS"

    async with phase7_factory() as session:
        intervention = (
            await session.scalars(
                select(CustomerIntervention).where(CustomerIntervention.merchant_id == merchant_id)
            )
        ).one()
        actions = (
            await session.scalars(
                select(RecoveryAction).where(RecoveryAction.merchant_id == merchant_id)
            )
        ).all()
        receipts = (
            await session.scalars(
                select(DecisionReceipt).where(DecisionReceipt.merchant_id == merchant_id)
            )
        ).all()

        assert intervention.status == "ACTIVE"
        assert sorted(intervention.coordinated_case_ids) == sorted(
            result.case_id for result in results if result.case_id is not None
        )
        assert len(actions) == 1
        assert actions[0].parameters["coordinated_case_ids"] == [ready.case_id]
        assert {receipt.scoring_artifact_classification for receipt in receipts} == {"SYNTHETIC"}
        assert all(receipt.scoring_model_version != "NOT_APPLICABLE" for receipt in receipts)
        assert all("risk_penalty_minor" in receipt.candidate_actions[0] for receipt in receipts)

    due_at = NOW + timedelta(days=2)
    async with phase7_factory.begin() as session:
        action = (
            await session.scalars(
                select(RecoveryAction).where(RecoveryAction.merchant_id == merchant_id)
            )
        ).one()
        action.status = "UNKNOWN"
        action.unknown_since = NOW
        action.reconciliation_deadline = due_at + timedelta(hours=1)

    async with phase7_factory.begin() as session:
        assert (
            await RecoveryRepository(session).close_expired_customer_interventions(
                due_at=due_at,
                limit=10,
            )
            == 0
        )

    async with phase7_factory.begin() as session:
        action = (
            await session.scalars(
                select(RecoveryAction).where(RecoveryAction.merchant_id == merchant_id)
            )
        ).one()
        action.status = "FAILED"
        action.unknown_since = None
        action.reconciliation_deadline = None
        await session.flush()
        assert (
            await RecoveryRepository(session).close_expired_customer_interventions(
                due_at=due_at,
                limit=10,
            )
            == 1
        )

    async with phase7_factory() as session:
        intervention = (
            await session.scalars(
                select(CustomerIntervention).where(CustomerIntervention.merchant_id == merchant_id)
            )
        ).one()
        assert intervention.status == "CLOSED"
        assert intervention.close_reason == "CONTACT_ACTION_FAILED"


async def test_incident_constraints_attach_only_to_diagnosis_correlated_cases(
    phase7_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_incident_correlation"
    async with phase7_factory.begin() as session:
        session.add(
            Merchant(
                id=merchant_id,
                display_name="Incident Correlation Merchant",
                provider="RAZORPAY",
                provider_account_id="account_incident_correlation",
                status="ACTIVE",
            )
        )
        await session.flush()
        session.add(
            Customer(
                merchant_id=merchant_id,
                id="customer_shared",
                provider_customer_id="customer_shared",
                provider_updated_at=NOW,
            )
        )
        session.add(
            PortfolioIncident(
                merchant_id=merchant_id,
                id="incident_issuer",
                scope="ISSUER",
                starts_at=NOW - timedelta(minutes=1),
                ends_at=NOW + timedelta(minutes=30),
                status="ACTIVE",
                dimension_key="UPI|BANK_A|ISSUER_UNAVAILABLE",
                payment_method="UPI",
                issuer_family="BANK_A",
                error_family="ISSUER_UNAVAILABLE",
                baseline_total=20,
                baseline_failures=2,
                current_total=10,
                current_failures=8,
                baseline_failure_rate_basis_points=1_000,
                current_failure_rate_basis_points=8_000,
                threshold_version="phase6-degradation-1.0",
                evidence={"classification": "SYNTHETIC"},
                clear_window_count=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        for case_id, diagnosis in (
            ("case_correlated", "ISSUER_TEMPORARILY_UNAVAILABLE"),
            ("case_unrelated", "INSUFFICIENT_FUNDS"),
        ):
            session.add(
                RecoveryCase(
                    merchant_id=merchant_id,
                    id=case_id,
                    schema_version="1.0",
                    workflow_type="FAILED_SUBSCRIPTION",
                    subject_type="SUBSCRIPTION",
                    subject_id=f"subscription_{case_id}",
                    customer_id="customer_shared",
                    revenue_at_risk_minor=10_000,
                    currency="INR",
                    state="POLICY_CHECK",
                    state_version=3,
                    diagnosis=diagnosis,
                    diagnosis_confidence_basis_points=8_000,
                    retry_count=0,
                    contact_count=0,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.flush()

        repository = RecoveryRepository(session)
        correlated = await repository.active_incidents(
            merchant_id=merchant_id,
            evaluated_at=NOW,
            case_id="case_correlated",
            diagnosis_code="ISSUER_TEMPORARILY_UNAVAILABLE",
        )
        unrelated = await repository.active_incidents(
            merchant_id=merchant_id,
            evaluated_at=NOW,
            case_id="case_unrelated",
            diagnosis_code="INSUFFICIENT_FUNDS",
        )

        assert [incident.incident_id for incident in correlated] == ["incident_issuer"]
        assert unrelated == ()
        links = (await session.scalars(select(IncidentCaseLink))).all()
        assert [(link.recovery_case_id, link.incident_id) for link in links] == [
            ("case_correlated", "incident_issuer")
        ]


async def test_scoring_failure_persists_a_conservative_fallback_receipt(
    phase7_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merchant_id = "merchant_scoring_fallback"
    async with phase7_factory.begin() as session:
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_merchant(
            merchant_id=merchant_id,
            display_name="Scoring Fallback Merchant",
            provider_account_id="account_scoring_fallback",
        )
        await ingestion.upsert_customer(
            merchant_id=merchant_id,
            customer_id="customer_fallback",
            provider_customer_id="customer_fallback",
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
                customer_id="customer_fallback",
                channel=ContactChannel.EMAIL.value,
                state="GRANTED",
                opted_out=False,
                source="TEST",
                effective_at=NOW,
            )
        )

    def fail_scoring(*_: object, **__: object) -> None:
        raise ArithmeticError("simulated deterministic inference failure")

    monkeypatch.setattr(
        recovery_service_module,
        "rank_candidates_by_expected_net_recovery",
        fail_scoring,
    )
    async with phase7_factory.begin() as session:
        result = await ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            clock=lambda: NOW,
        ).ingest_overdue_invoice(
            merchant_id=merchant_id,
            source_event_id="invoice-overdue-fallback",
            correlation_id="correlation-fallback",
            invoice_id="invoice_fallback",
            customer_id="customer_fallback",
            amount_minor=10_000,
            outstanding_amount_minor=10_000,
            currency="INR",
            due_at=NOW - timedelta(days=1),
            occurred_at=NOW,
            received_at=NOW,
        )
        assert result.receipt_id is not None

    async with phase7_factory() as session:
        receipt = await session.get(DecisionReceipt, (merchant_id, result.receipt_id))
        assert receipt is not None
        assert receipt.scoring_fallback_reason == "SCORING_INFERENCE_FAILED"
        assert receipt.scoring_artifact_classification == "SYNTHETIC"


async def test_scheduled_portfolio_evaluation_resolves_incident_without_new_events(
    phase7_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_scheduled_portfolio"
    async with phase7_factory.begin() as session:
        await EventIngestionRepository(session).upsert_merchant(
            merchant_id=merchant_id,
            display_name="Scheduled Portfolio Merchant",
            provider_account_id="account_scheduled_portfolio",
        )

    observations = []
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
                occurred_at=NOW - timedelta(hours=1, minutes=index),
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
                occurred_at=NOW - timedelta(minutes=index),
            )
        )

    async with phase7_factory.begin() as session:
        repository = PlaybookRepository(session)
        for index, observation in enumerate(observations[:-1]):
            await repository.record_payment_observation(
                observation,
                source_event_id=f"source-{index}",
            )
        await PaymentDegradationService(repository).observe_and_evaluate(
            observations[-1],
            source_event_id="source-final",
            evaluated_at=NOW,
        )

    for evaluated_at in (NOW + timedelta(minutes=16), NOW + timedelta(minutes=32)):
        async with phase7_factory.begin() as session:
            result = await PaymentDegradationService(
                PlaybookRepository(session)
            ).maintain_portfolios(evaluated_at=evaluated_at, merchant_limit=10)
            assert result.merchants_evaluated == 1

    async with phase7_factory() as session:
        incident = (
            await session.scalars(
                select(PortfolioIncident).where(PortfolioIncident.merchant_id == merchant_id)
            )
        ).one()
        assert incident.status == "RESOLVED"
        assert incident.clear_window_count == 2
        assert incident.resolution_reason == "FAILURE_RATE_RECOVERED"
