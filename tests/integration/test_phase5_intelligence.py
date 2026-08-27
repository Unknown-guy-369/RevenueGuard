"""PostgreSQL integration coverage for bounded agent recommendations."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel
from revenueguard_agents import BoundedCaseIntelligence, ModelResponse
from revenueguard_domain import ActionType, CaseState, conservative_default_policy
from revenueguard_integrations.persistence import (
    Base,
    DecisionReceipt,
    EventIngestionRepository,
    ModelPrediction,
    RecoveryAction,
    RecoveryRepository,
    create_session_factory,
)
from revenueguard_integrations.recovery import RecoveryApplicationService
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
NOW = datetime(2026, 8, 27, 11, tzinfo=UTC)


class SuccessfulModel:
    @property
    def model_version(self) -> str:
        return "phase5-integration-model-1"

    async def generate(
        self,
        *,
        node: str,
        payload: Mapping[str, object],
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> ModelResponse:
        del payload, response_schema, max_output_tokens
        responses: dict[str, Mapping[str, object]] = {
            "DIAGNOSIS_ASSISTANCE": {
                "diagnosis_code": "EXPIRED_PAYMENT_METHOD",
                "confidence_basis_points": 9_500,
                "rationale": "The normalized evidence supports the deterministic diagnosis.",
            },
            "STRATEGY_GENERATION": {
                "strategies": [
                    {
                        "action_type": "REQUEST_PAYMENT_METHOD_UPDATE",
                        "recovery_probability_basis_points": 9_000,
                        "expected_net_recovery_minor": 9_000,
                        "channel": "EMAIL",
                    },
                    {
                        "action_type": "CREATE_PAYMENT_LINK",
                        "recovery_probability_basis_points": 7_000,
                        "expected_net_recovery_minor": 6_000,
                        "channel": None,
                    },
                ]
            },
            "RANKING": {
                "ordered_action_types": [
                    "REQUEST_PAYMENT_METHOD_UPDATE",
                    "CREATE_PAYMENT_LINK",
                    "NO_ACTION",
                ]
            },
            "EXPLANATION": {
                "explanation": "Email ranks first on recovery, subject to deterministic consent."
            },
        }
        return ModelResponse(payload=responses[node], input_tokens=20, output_tokens=30)


@pytest.fixture
async def phase5_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"phase5_test_{uuid4().hex}"
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


async def test_predictions_are_persisted_before_policy_authorizes_outbox(
    phase5_factory: async_sessionmaker[AsyncSession],
) -> None:
    merchant_id = "merchant_phase5"
    async with phase5_factory.begin() as session:
        ingestion = EventIngestionRepository(session)
        await ingestion.upsert_merchant(
            merchant_id=merchant_id,
            display_name="Phase 5 Merchant",
            provider_account_id="account_phase5",
        )
        await RecoveryRepository(session).publish_policy(
            merchant_id=merchant_id,
            policy=conservative_default_policy(),
            published_by="TEST",
        )
        await ingestion.upsert_customer(
            merchant_id=merchant_id,
            customer_id="customer_phase5",
            provider_customer_id="customer_phase5",
            provider_updated_at=NOW,
        )
        await ingestion.upsert_payment(
            merchant_id=merchant_id,
            payment_id="payment_phase5",
            provider_payment_id="payment_phase5",
            customer_id="customer_phase5",
            order_id=None,
            amount_minor=10_000,
            currency="INR",
            status="FAILED",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        await ingestion.upsert_subscription(
            merchant_id=merchant_id,
            subscription_id="subscription_phase5",
            provider_subscription_id="subscription_phase5",
            customer_id="customer_phase5",
            amount_minor=10_000,
            currency="INR",
            status="PENDING",
            provider_occurred_at=NOW,
            provider_updated_at=NOW,
        )
        webhook = await ingestion.record_webhook(
            event_id=str(uuid4()),
            merchant_id=merchant_id,
            provider="RAZORPAY",
            provider_event_id="provider_phase5",
            event_type="payment.failed",
            entity_id="payment_phase5",
            raw_body=b"{}",
            raw_payload={},
            occurred_at=NOW,
            received_at=NOW,
            correlation_id="correlation_phase5",
        )
        normalized = await ingestion.persist_normalized_event(
            event={
                "schema_version": "1.0",
                "event_id": "event_phase5",
                "merchant_id": merchant_id,
                "source": "RAZORPAY",
                "source_event_id": "provider_phase5",
                "event_type": "payment.failed",
                "occurred_at": NOW,
                "received_at": NOW,
                "customer_id": "customer_phase5",
                "payment_id": "payment_phase5",
                "order_id": None,
                "subscription_id": "subscription_phase5",
                "invoice_id": None,
                "payment_link_id": None,
                "amount_minor": 10_000,
                "currency": "INR",
                "failure_code": "BAD_REQUEST_ERROR",
                "normalized_failure_category": "EXPIRED_PAYMENT_METHOD",
                "correlation_id": "correlation_phase5",
                "causation_id": None,
                "source_payload_reference": "webhook_events/provider_phase5",
            },
            webhook_event_id=webhook.event.id,
        )
        ids = iter(("case_phase5", "receipt_phase5"))
        result = await RecoveryApplicationService(
            RecoveryRepository(session),
            case_intelligence=BoundedCaseIntelligence(SuccessfulModel()),
            clock=lambda: NOW,
            id_generator=lambda _prefix: next(ids),
        ).process_event(merchant_id=merchant_id, normalized_event_id=normalized.id)

        assert result.case_state is CaseState.READY
        predictions = list(
            (
                await session.scalars(
                    select(ModelPrediction).where(ModelPrediction.merchant_id == merchant_id)
                )
            ).all()
        )
        receipt = (
            await session.scalars(
                select(DecisionReceipt).where(DecisionReceipt.merchant_id == merchant_id)
            )
        ).one()
        action = (
            await session.scalars(
                select(RecoveryAction).where(RecoveryAction.merchant_id == merchant_id)
            )
        ).one()

        assert len(predictions) == 4
        assert {prediction.status for prediction in predictions} == {"SUCCEEDED"}
        assert set(receipt.model_prediction_ids) == {item.id for item in predictions}
        assert receipt.version_bundle["model"] == "phase5-integration-model-1"
        assert receipt.version_bundle["schema"] == "1.0"
        assert {prediction.schema_version for prediction in predictions} == {
            "phase5-agent-schema-1.0"
        }
        assert receipt.selected_action_type == ActionType.CREATE_PAYMENT_LINK.value
        assert receipt.policy_reason_codes == [
            "CHANNEL_CONSENT_NOT_GRANTED",
            "POLICY_AUTHORIZED",
        ]
        assert action.action_type == ActionType.CREATE_PAYMENT_LINK.value
