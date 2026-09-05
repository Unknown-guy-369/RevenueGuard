from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from revenueguard_api.merchant_dashboard import (
    MerchantDashboardConflictError,
    SimulationCreateRequest,
)
from revenueguard_api.merchant_dashboard_persistence import DatabaseMerchantDashboardService
from revenueguard_integrations.persistence import (
    Base,
    EventIngestionRepository,
    PaymentOutcomeObservation,
    SimulationSession,
    WebhookEvent,
    create_session_factory,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
MERCHANT_ID = "merchant_dashboard_001"
NOW = datetime(2026, 8, 29, 9, tzinfo=UTC)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"merchant_dashboard_{uuid4().hex}"
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


async def _merchant(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        await EventIngestionRepository(session).upsert_merchant(
            merchant_id=MERCHANT_ID,
            display_name="Merchant dashboard",
            provider_account_id="account_dashboard_001",
        )


async def test_synthetic_payments_are_labelled_and_excluded_from_business_totals(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _merchant(session_factory)
    service = DatabaseMerchantDashboardService(
        session_factory,
        clock=lambda: NOW,
        token_factory=lambda: "fixed_dashboard_session",
        simulator_secret="simulator-test-secret",
    )
    simulation = await service.create_simulation(
        MERCHANT_ID,
        SimulationCreateRequest(
            scenario="INSUFFICIENT_FUNDS",
            flow_type="ONE_TIME",
            amount_minor=50_000,
            currency="INR",
        ),
    )

    async with session_factory.begin() as session:
        repository = EventIngestionRepository(session)
        await repository.upsert_payment(
            merchant_id=MERCHANT_ID,
            payment_id="payment_real_001",
            provider_payment_id="pay_real_001",
            customer_id=None,
            order_id=None,
            amount_minor=10_000,
            currency="INR",
            status="CAPTURED",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        simulation_row = await session.scalar(
            select(SimulationSession).where(SimulationSession.id == simulation.simulation_id)
        )
        assert simulation_row is not None
        await repository.upsert_payment(
            merchant_id=MERCHANT_ID,
            payment_id=simulation_row.payment_id,
            provider_payment_id="pay_simulated_durable",
            customer_id=None,
            order_id=None,
            amount_minor=50_000,
            currency="INR",
            status="FAILED",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        session.add_all(
            [
                PaymentOutcomeObservation(
                    merchant_id=MERCHANT_ID,
                    id="observation_real_001",
                    payment_id="payment_real_001",
                    source_event_id="event_real_001",
                    succeeded=True,
                    payment_method="card",
                    issuer_family="issuer_real",
                    error_family="none",
                    occurred_at=NOW,
                ),
                PaymentOutcomeObservation(
                    merchant_id=MERCHANT_ID,
                    id="observation_sim_001",
                    payment_id=simulation_row.payment_id,
                    source_event_id="event_sim_001",
                    succeeded=False,
                    payment_method="upi",
                    issuer_family="issuer_simulated",
                    error_family="insufficient_funds",
                    occurred_at=NOW,
                ),
            ]
        )

    overview = await service.business_overview(MERCHANT_ID, since=NOW - timedelta(days=1))
    ledger = await service.payments(MERCHANT_ID, statuses=(), query=None, limit=20, offset=0)

    assert len(overview.currency_totals) == 1
    totals = overview.currency_totals[0]
    assert totals.gross_volume_minor == 10_000
    assert totals.collected_minor == 10_000
    assert totals.failed_value_minor == 0
    assert [
        (item.payment_method, item.share_basis_points) for item in overview.payment_methods
    ] == [("card", 10_000)]
    by_provider = {item.provider_reference_masked: item for item in ledger.payments}
    assert (
        next(item.classification for item in by_provider.values() if item.amount_minor == 50_000)
        == "SYNTHETIC"
    )


async def test_simulation_submission_is_durable_signed_and_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _merchant(session_factory)
    service = DatabaseMerchantDashboardService(
        session_factory,
        clock=lambda: NOW,
        token_factory=lambda: "idempotent_session",
        simulator_secret="simulator-test-secret",
    )
    created = await service.create_simulation(
        MERCHANT_ID,
        SimulationCreateRequest(
            scenario="ISSUER_OUTAGE",
            flow_type="SUBSCRIPTION",
            amount_minor=24_990,
            currency="INR",
        ),
    )

    first = await service.submit_simulation(created.simulation_id)
    second = await service.submit_simulation(created.simulation_id)

    assert first == second
    assert first.classification == "SYNTHETIC"
    async with session_factory() as session:
        events = (
            await session.scalars(
                select(WebhookEvent).where(
                    WebhookEvent.merchant_id == MERCHANT_ID,
                    WebhookEvent.provider == "SIMULATOR",
                )
            )
        ).all()
        assert len(events) == 1
        assert events[0].signature_valid is True
        assert events[0].raw_payload["event"] == "subscription.pending"


async def test_authentication_failure_simulation_is_signed_and_normalizable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _merchant(session_factory)
    service = DatabaseMerchantDashboardService(
        session_factory,
        clock=lambda: NOW,
        token_factory=lambda: "authentication_session",
        simulator_secret="simulator-test-secret",
    )
    created = await service.create_simulation(
        MERCHANT_ID,
        SimulationCreateRequest(
            scenario="AUTHENTICATION_FAILURE",
            flow_type="ONE_TIME",
            amount_minor=24_990,
            currency="INR",
        ),
    )

    submitted = await service.submit_simulation(created.simulation_id)

    async with session_factory() as session:
        webhook = await session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.merchant_id == MERCHANT_ID,
                WebhookEvent.provider_event_id == submitted.provider_event_id,
            )
        )
        assert webhook is not None
        assert webhook.signature_valid is True
        assert webhook.raw_payload["event"] == "payment.failed"
        payment = webhook.raw_payload["payload"]["payment"]["entity"]
        assert payment["error_reason"] == "authentication_failed"


async def test_expired_simulation_is_persisted_before_conflict_is_returned(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _merchant(session_factory)
    clock = [NOW]
    service = DatabaseMerchantDashboardService(
        session_factory,
        clock=lambda: clock[0],
        token_factory=lambda: "expired_session",
        simulator_secret="simulator-test-secret",
    )
    created = await service.create_simulation(
        MERCHANT_ID,
        SimulationCreateRequest(
            scenario="TIMEOUT",
            flow_type="ONE_TIME",
            amount_minor=1_000,
            currency="INR",
        ),
    )
    clock[0] = NOW + timedelta(hours=2)

    with pytest.raises(MerchantDashboardConflictError, match="expired"):
        await service.submit_simulation(created.simulation_id)

    async with session_factory() as session:
        row = await session.scalar(
            select(SimulationSession).where(SimulationSession.id == created.simulation_id)
        )
        assert row is not None
        assert row.status == "EXPIRED"
        assert row.provider_event_id is None
