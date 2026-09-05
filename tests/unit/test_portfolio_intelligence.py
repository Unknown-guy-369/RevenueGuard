from __future__ import annotations

from datetime import UTC, datetime

import pytest
from revenueguard_domain import (
    ActionEconomics,
    ActionType,
    ArtifactClassification,
    CandidateAction,
    ContactChannel,
    CustomerIntervention,
    CustomerInterventionStatus,
    LogisticScoringArtifact,
    RecoveryScoringContext,
    rank_candidates_by_expected_net_recovery,
)


def test_expected_net_recovery_ranking_uses_integer_money_and_policy_allowlist() -> None:
    candidates = (
        CandidateAction(
            action_type=ActionType.SEND_REMINDER,
            recovery_probability_basis_points=9_900,
            expected_net_recovery_minor=99_999,
            rank=1,
            target="customer_001",
            channel=ContactChannel.EMAIL,
        ),
        CandidateAction(
            action_type=ActionType.DEFER_RETRY,
            recovery_probability_basis_points=100,
            expected_net_recovery_minor=-99_999,
            rank=2,
            target="payment_001",
        ),
        CandidateAction(
            action_type=ActionType.CREATE_PAYMENT_LINK,
            recovery_probability_basis_points=9_000,
            expected_net_recovery_minor=80_000,
            rank=3,
            target="payment_001",
        ),
        CandidateAction(
            action_type=ActionType.NO_ACTION,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=4,
            target="case_001",
        ),
    )
    artifact = LogisticScoringArtifact.zero_weighted(
        model_version="phase7-test-logit-1",
        feature_version="phase7-test-features-1",
        classification=ArtifactClassification.SYNTHETIC,
    )
    economics = ActionEconomics(
        version="phase7-test-economics-1",
        action_cost_minor={
            ActionType.SEND_REMINDER: 300,
            ActionType.DEFER_RETRY: 100,
        },
        risk_penalty_minor={ActionType.SEND_REMINDER: 200},
        customer_friction_penalty_minor={ActionType.SEND_REMINDER: 500},
    )

    result = rank_candidates_by_expected_net_recovery(
        candidates,
        context=RecoveryScoringContext(
            amount_minor=10_001,
            retry_count=0,
            aggregate_contact_count=0,
            diagnosis_confidence_basis_points=8_000,
            failure_category="INSUFFICIENT_FUNDS",
            active_systemic_incident=False,
            evaluated_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
        ),
        artifact=artifact,
        economics=economics,
        allowed_actions=frozenset(
            {ActionType.SEND_REMINDER, ActionType.DEFER_RETRY, ActionType.NO_ACTION}
        ),
    )

    assert [item.action_type for item in result.candidates] == [
        ActionType.DEFER_RETRY,
        ActionType.SEND_REMINDER,
        ActionType.NO_ACTION,
    ]
    assert result.candidates[0].recovery_probability_basis_points == 5_000
    assert result.candidates[0].expected_net_recovery_minor == 4_900
    assert result.candidates[1].expected_net_recovery_minor == 4_000
    assert result.candidates[1].action_cost_minor == 300
    assert result.candidates[1].risk_penalty_minor == 200
    assert result.candidates[1].customer_friction_penalty_minor == 500
    assert result.model_version == "phase7-test-logit-1"
    assert result.artifact_classification is ArtifactClassification.SYNTHETIC
    assert result.fallback_reason is None


def test_active_customer_intervention_requires_owner_in_coordinated_cases() -> None:
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)

    intervention = CustomerIntervention(
        intervention_id="intervention_001",
        merchant_id="merchant_001",
        customer_id="customer_001",
        owner_case_id="case_001",
        action_id="action_001",
        coordinated_case_ids=("case_001", "case_002"),
        status=CustomerInterventionStatus.ACTIVE,
        cooldown_until=datetime(2026, 9, 5, 13, tzinfo=UTC),
        model_version="phase7-test-logit-1",
        policy_version="policy-v1",
        created_at=now,
        updated_at=now,
    )

    assert intervention.coordinated_case_ids == ("case_001", "case_002")


def test_economic_sorting_does_not_displace_an_explicit_human_escalation() -> None:
    candidates = (
        CandidateAction(
            action_type=ActionType.ESCALATE_HUMAN,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=1,
            target="case_001",
        ),
        CandidateAction(
            action_type=ActionType.DEFER_RETRY,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=2,
            target="payment_001",
        ),
        CandidateAction(
            action_type=ActionType.NO_ACTION,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=3,
            target="case_001",
        ),
    )

    result = rank_candidates_by_expected_net_recovery(
        candidates,
        context=RecoveryScoringContext(
            amount_minor=10_000,
            retry_count=0,
            aggregate_contact_count=0,
            diagnosis_confidence_basis_points=8_000,
            failure_category="UNKNOWN",
            active_systemic_incident=False,
            evaluated_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
        ),
        artifact=LogisticScoringArtifact.zero_weighted(
            model_version="phase7-test-logit-1",
            feature_version="phase7-test-features-1",
            classification=ArtifactClassification.SYNTHETIC,
        ),
        economics=ActionEconomics(
            version="phase7-test-economics-1",
            action_cost_minor={},
            risk_penalty_minor={},
            customer_friction_penalty_minor={},
        ),
        allowed_actions=frozenset({ActionType.DEFER_RETRY, ActionType.NO_ACTION}),
    )

    assert [candidate.action_type for candidate in result.candidates] == [
        ActionType.ESCALATE_HUMAN,
        ActionType.DEFER_RETRY,
        ActionType.NO_ACTION,
    ]


def test_logistic_artifact_rejects_an_unversioned_feature_shape() -> None:
    with pytest.raises(ValueError, match="exactly match"):
        LogisticScoringArtifact(
            model_version="phase7-test-logit-1",
            feature_version="phase7-test-features-1",
            classification=ArtifactClassification.SYNTHETIC,
            intercept_millilogits=0,
            feature_weights_millilogits={"amount_bucket": 1},
            action_bias_millilogits={},
            failure_category_bias_millilogits={},
        )
