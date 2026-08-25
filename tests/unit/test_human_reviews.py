from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from revenueguard_domain import (
    ActionFingerprintInput,
    HumanReviewDecision,
    HumanReviewRequest,
    ReviewDecisionType,
    ReviewStatus,
    decide_review,
    expire_review,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
POLICY_DIGEST = "a" * 64


def fingerprint(**changes: object) -> str:
    values: dict[str, object] = {
        "case_id": "case_001",
        "action_type": "SEND_REMINDER",
        "target": "customer_001",
        "amount_minor": 10_000,
        "currency": "INR",
        "logical_attempt": 1,
        "policy_digest": POLICY_DIGEST,
    }
    values.update(changes)
    return ActionFingerprintInput(**values).digest()  # type: ignore[arg-type]


def make_request() -> HumanReviewRequest:
    return HumanReviewRequest(
        review_id="review_001",
        merchant_id="merchant_001",
        case_id="case_001",
        action_fingerprint=fingerprint(),
        proposed_action_type="SEND_REMINDER",
        evidence_references=("event_001",),
        policy_version="policy-v1",
        policy_digest=POLICY_DIGEST,
        reason_code="HUMAN_REVIEW_REQUIRED",
        risk_detail="high amount",
        requested_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def test_action_fingerprint_is_deterministic_and_binds_every_required_field() -> None:
    baseline = fingerprint()

    assert baseline == fingerprint()
    assert len(baseline) == 64
    for change in (
        {"case_id": "case_002"},
        {"action_type": "CREATE_PAYMENT_LINK"},
        {"target": "customer_002"},
        {"amount_minor": 10_001},
        {"currency": "USD"},
        {"logical_attempt": 2},
        {"policy_digest": "b" * 64},
    ):
        assert fingerprint(**change) != baseline


def test_review_can_be_approved_with_required_audit_data() -> None:
    request = make_request()
    approved = decide_review(
        request,
        HumanReviewDecision(
            review_id=request.review_id,
            decision=ReviewDecisionType.APPROVE,
            reviewer_id="operator_001",
            rationale="verified merchant request",
            decided_at=NOW + timedelta(minutes=10),
        ),
    )

    assert approved.status is ReviewStatus.APPROVED
    assert approved.reviewer_id == "operator_001"
    assert approved.is_matching_approval(
        fingerprint=request.action_fingerprint,
        policy_digest=POLICY_DIGEST,
        evaluated_at=NOW + timedelta(minutes=20),
    )


def test_approval_does_not_match_changed_action_policy_or_expiry() -> None:
    request = make_request()
    approved = decide_review(
        request,
        HumanReviewDecision(
            review_id=request.review_id,
            decision=ReviewDecisionType.APPROVE,
            reviewer_id="operator_001",
            rationale="approved",
            decided_at=NOW + timedelta(minutes=1),
        ),
    )

    assert not approved.is_matching_approval(
        fingerprint=fingerprint(amount_minor=10_001),
        policy_digest=POLICY_DIGEST,
        evaluated_at=NOW + timedelta(minutes=2),
    )
    assert not approved.is_matching_approval(
        fingerprint=approved.action_fingerprint,
        policy_digest="b" * 64,
        evaluated_at=NOW + timedelta(minutes=2),
    )
    assert not approved.is_matching_approval(
        fingerprint=approved.action_fingerprint,
        policy_digest=POLICY_DIGEST,
        evaluated_at=approved.expires_at,
    )


def test_review_rejection_and_expiry_are_terminal_review_decisions() -> None:
    rejected = decide_review(
        make_request(),
        HumanReviewDecision(
            review_id="review_001",
            decision=ReviewDecisionType.REJECT,
            reviewer_id="operator_001",
            rationale="declined",
            decided_at=NOW + timedelta(minutes=1),
        ),
    )
    expired = expire_review(make_request(), expired_at=NOW + timedelta(hours=1))

    assert rejected.status is ReviewStatus.REJECTED
    assert expired.status is ReviewStatus.EXPIRED
    assert expired.rationale == "REVIEW_EXPIRED"
    with pytest.raises(ValueError, match="requested"):
        decide_review(
            rejected,
            HumanReviewDecision(
                review_id="review_001",
                decision=ReviewDecisionType.APPROVE,
                reviewer_id="operator_002",
                rationale="second decision",
                decided_at=NOW + timedelta(minutes=2),
            ),
        )


def test_expired_or_mismatched_review_cannot_be_decided() -> None:
    with pytest.raises(ValueError, match="expired"):
        decide_review(
            make_request(),
            HumanReviewDecision(
                review_id="review_001",
                decision=ReviewDecisionType.APPROVE,
                reviewer_id="operator_001",
                rationale="late",
                decided_at=NOW + timedelta(hours=1),
            ),
        )
    with pytest.raises(ValueError, match="different"):
        decide_review(
            make_request(),
            HumanReviewDecision(
                review_id="review_other",
                decision=ReviewDecisionType.REJECT,
                reviewer_id="operator_001",
                rationale="wrong request",
                decided_at=NOW + timedelta(minutes=1),
            ),
        )


def test_requested_review_rejects_decision_metadata() -> None:
    with pytest.raises(ValueError, match="cannot contain"):
        replace(make_request(), reviewer_id="operator_001")


def test_review_decision_cannot_precede_request() -> None:
    request = make_request()
    with pytest.raises(ValueError, match="precede"):
        decide_review(
            request,
            HumanReviewDecision(
                review_id=request.review_id,
                decision=ReviewDecisionType.APPROVE,
                reviewer_id="operator_001",
                rationale="invalid chronology",
                decided_at=request.requested_at - timedelta(seconds=1),
            ),
        )
