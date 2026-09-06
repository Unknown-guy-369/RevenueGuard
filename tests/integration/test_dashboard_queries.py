"""PostgreSQL integration coverage for merchant-scoped dashboard reads."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from revenueguard_api.dashboard import DashboardNotFoundError
from revenueguard_api.dashboard_persistence import DatabaseDashboardQueryService
from revenueguard_integrations.persistence import (
    ActionAttempt,
    Base,
    DecisionReceipt,
    Merchant,
    MerchantPolicyVersion,
    RecoveryAction,
    RecoveryCase,
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


@pytest.fixture
async def dashboard_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"dashboard_test_{uuid4().hex}"
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


async def test_dashboard_queries_are_authoritative_and_tenant_scoped(
    dashboard_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    async with dashboard_factory.begin() as session:
        session.add_all(
            [
                Merchant(id="merchant_one", display_name="Merchant One"),
                Merchant(id="merchant_two", display_name="Merchant Two"),
                RecoveryCase(
                    merchant_id="merchant_one",
                    id="case_one",
                    workflow_type="FAILED_SUBSCRIPTION",
                    subject_type="SUBSCRIPTION",
                    subject_id="subscription_raw_secret_one",
                    revenue_at_risk_minor=10_000,
                    currency="INR",
                    state="VERIFYING",
                ),
                RecoveryCase(
                    merchant_id="merchant_two",
                    id="case_two",
                    workflow_type="FAILED_SUBSCRIPTION",
                    subject_type="SUBSCRIPTION",
                    subject_id="subscription_raw_secret_two",
                    revenue_at_risk_minor=999_999,
                    currency="INR",
                    state="UNKNOWN",
                ),
                MerchantPolicyVersion(
                    merchant_id="merchant_one",
                    version="test-policy-v1",
                    snapshot={"classification": "TEST"},
                    content_sha256="a" * 64,
                    published_by="test-suite",
                    effective_at=now,
                    created_at=now,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                DecisionReceipt(
                    merchant_id="merchant_one",
                    id="decision_one",
                    recovery_case_id="case_one",
                    correlation_id="correlation_one",
                    evidence_references=["event_one"],
                    candidate_actions=[{"action_type": "CREATE_PAYMENT_LINK"}],
                    selected_action_type="CREATE_PAYMENT_LINK",
                    explanation="Generate a Test Mode payment link after policy authorization.",
                    policy_result="PROCEED",
                    policy_reason_codes=["POLICY_AUTHORIZED"],
                    policy_version="test-policy-v1",
                    version_bundle={"policy": "test-policy-v1"},
                    resulting_action_id="action_one",
                    resulting_state="READY",
                    model_prediction_ids=[],
                    created_at=now,
                ),
                RecoveryAction(
                    merchant_id="merchant_one",
                    id="action_one",
                    recovery_case_id="case_one",
                    decision_receipt_id="decision_one",
                    action_type="CREATE_PAYMENT_LINK",
                    target_type="PAYMENT",
                    target_id="payment_one",
                    logical_attempt=1,
                    idempotency_key="rg:v1:merchant-one:case-one:payment-link:1",
                    status="SUCCEEDED",
                    parameters={"amount_minor": 10_000, "currency": "INR"},
                    policy_version="test-policy-v1",
                    correlation_id="correlation_one",
                    authorized_at=now,
                    execute_after=now,
                    next_attempt_at=now,
                    attempt_count=1,
                    max_attempts=3,
                ),
            ]
        )
        await session.flush()
        session.add(
            ActionAttempt(
                id="attempt_one",
                merchant_id="merchant_one",
                recovery_action_id="action_one",
                attempt_number=1,
                request_id="request_one",
                lease_token="lease_one",
                started_at=now,
                completed_at=now,
                outcome_status="SUCCEEDED",
                response_category="API_ACCEPTED",
                provider_object_id="plink_test_one",
                provider_status_code=200,
                response_reference="https://rzp.io/i/test-payment",
                retryable=False,
            )
        )

    service = DatabaseDashboardQueryService(dashboard_factory)
    overview = await service.overview("merchant_one")
    listed = await service.list_cases("merchant_one", states=(), limit=25)
    detail = await service.case_detail("merchant_one", "case_one")

    assert overview.context.merchant_id == "merchant_one"
    assert overview.counts.active_cases == 1
    assert overview.counts.unknown_cases == 0
    assert overview.currency_totals[0].revenue_at_risk_minor == 10_000
    assert listed.total == 1
    assert listed.cases[0].case_id == "case_one"
    assert listed.cases[0].subject_reference_masked.startswith("SUBSCRIPTION · ")
    assert "subscription_raw_secret_one" not in listed.model_dump_json()
    assert detail.case.case_id == "case_one"
    assert detail.actions[0].payment_link_url == "https://rzp.io/i/test-payment"

    with pytest.raises(DashboardNotFoundError):
        await service.case_detail("merchant_one", "case_two")
