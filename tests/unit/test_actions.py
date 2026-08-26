from __future__ import annotations

from datetime import UTC, datetime

import pytest
from revenueguard_domain import (
    ActionStatus,
    ActionType,
    EvidenceSource,
    RecoveryAction,
    SubjectType,
    VerifiedOutcome,
    action_idempotency_key,
)

NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)


def _key(logical_attempt: int = 1) -> str:
    return action_idempotency_key(
        merchant_id="merchant_001",
        case_id="case_001",
        action_type=ActionType.CREATE_PAYMENT_LINK,
        target_type=SubjectType.SUBSCRIPTION,
        target_id="subscription_001",
        logical_attempt=logical_attempt,
    )


def test_action_idempotency_is_stable_and_bound_to_logical_identity() -> None:
    assert _key() == _key()
    assert _key(1) != _key(2)
    assert _key().startswith("rg:v1:")


def test_recovery_action_rejects_a_worker_specific_or_mismatched_key() -> None:
    with pytest.raises(ValueError, match="logical action identity"):
        RecoveryAction(
            action_id="action_001",
            case_id="case_001",
            merchant_id="merchant_001",
            decision_receipt_id="receipt_001",
            action_type=ActionType.CREATE_PAYMENT_LINK,
            target_type=SubjectType.SUBSCRIPTION,
            target_id="subscription_001",
            logical_attempt=1,
            idempotency_key="worker-attempt-1234567890",
            status=ActionStatus.PENDING,
            parameters={"amount_minor": 10_000, "currency": "INR", "provider_mode": "TEST"},
            authorized_at=NOW,
            execute_after=NOW,
            created_at=NOW,
        )


def test_unknown_and_unverified_outcomes_cannot_count_recovered_money() -> None:
    with pytest.raises(ValueError, match="UNKNOWN outcomes"):
        VerifiedOutcome(
            outcome_id="outcome_001",
            action_id="action_001",
            case_id="case_001",
            merchant_id="merchant_001",
            outcome_status=ActionStatus.UNKNOWN,
            is_authoritative=False,
            evidence_source=EvidenceSource.NONE,
            recovered_amount_minor=1,
            currency="INR",
            observed_at=NOW,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="authoritative success"):
        VerifiedOutcome(
            outcome_id="outcome_002",
            action_id="action_001",
            case_id="case_001",
            merchant_id="merchant_001",
            outcome_status=ActionStatus.SUCCEEDED,
            is_authoritative=False,
            evidence_source=EvidenceSource.PROVIDER_RESPONSE,
            recovered_amount_minor=10_000,
            currency="INR",
            observed_at=NOW,
            created_at=NOW,
        )


def test_authoritative_success_preserves_integer_money_and_evidence() -> None:
    outcome = VerifiedOutcome(
        outcome_id="outcome_001",
        action_id="action_001",
        case_id="case_001",
        merchant_id="merchant_001",
        outcome_status=ActionStatus.SUCCEEDED,
        is_authoritative=True,
        evidence_source=EvidenceSource.SIGNED_WEBHOOK,
        evidence_reference="webhook_events/event_001",
        recovered_amount_minor=10_000,
        currency="INR",
        observed_at=NOW,
        verified_at=NOW,
        created_at=NOW,
    )
    assert outcome.to_dict()["recovered_amount_minor"] == 10_000
