from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from revenueguard_domain import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    CaseState,
    CaseTransitionError,
    RecoveryCase,
    StaleCaseVersionError,
    SubjectType,
    WorkflowType,
    can_transition,
    transition_case,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


def make_case(state: CaseState = CaseState.DETECTED) -> RecoveryCase:
    return RecoveryCase(
        case_id="case_001",
        merchant_id="merchant_001",
        workflow_type=WorkflowType.PAYMENT_DEGRADATION,
        subject_type=SubjectType.PAYMENT,
        subject_id="payment_001",
        revenue_at_risk_minor=10_000,
        currency="INR",
        state=state,
        state_version=1,
        retry_count=0,
        contact_count=0,
        created_at=NOW,
        updated_at=NOW,
        terminal_reason="TEST_TERMINAL" if state in TERMINAL_STATES else None,
    )


def test_transition_map_contains_every_state_and_locks_terminal_states() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(CaseState)
    assert set(TERMINAL_STATES) == {CaseState.RECOVERED, CaseState.STOPPED}
    assert ALLOWED_TRANSITIONS[CaseState.RECOVERED] == frozenset()
    assert ALLOWED_TRANSITIONS[CaseState.STOPPED] == frozenset()


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (before, after)
        for before, allowed in ALLOWED_TRANSITIONS.items()
        for after in sorted(allowed, key=lambda state: state.value)
    ],
)
def test_every_frozen_transition_is_accepted(before: CaseState, after: CaseState) -> None:
    case = make_case(before)

    updated, evidence = transition_case(
        case,
        expected_version=1,
        to_state=after,
        actor="WORKER",
        reason_code="TEST_TRANSITION",
        reason_detail="contract edge",
        correlation_id="correlation_001",
        policy_version="policy-v1",
        authoritative_evidence_reference=(
            "provider/events/verified_001" if after is CaseState.RECOVERED else None
        ),
        occurred_at=NOW + timedelta(seconds=1),
        terminal_reason="TEST_TERMINAL" if after in TERMINAL_STATES else None,
    )

    assert can_transition(before, after)
    assert updated.state is after
    assert updated.state_version == 2
    assert evidence.before_state is before
    assert evidence.after_state is after
    assert evidence.before_version == 1
    assert evidence.after_version == 2
    assert evidence.actor == "WORKER"
    assert evidence.reason_code == "TEST_TRANSITION"
    assert evidence.correlation_id == "correlation_001"
    assert evidence.policy_version == "policy-v1"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (before, after)
        for before in CaseState
        for after in CaseState
        if after not in ALLOWED_TRANSITIONS[before]
    ],
)
def test_every_transition_outside_frozen_graph_is_rejected(
    before: CaseState, after: CaseState
) -> None:
    case = make_case(before)

    assert not can_transition(before, after)
    with pytest.raises(CaseTransitionError, match="cannot transition") as error:
        transition_case(
            case,
            expected_version=1,
            to_state=after,
            actor="WORKER",
            reason_code="INVALID",
            correlation_id="correlation_001",
            policy_version="policy-v1",
            occurred_at=NOW + timedelta(seconds=1),
            terminal_reason="TEST_TERMINAL" if after in TERMINAL_STATES else None,
        )
    assert error.value.code == "ILLEGAL_CASE_TRANSITION"


def test_stale_expected_version_is_rejected_before_transition() -> None:
    with pytest.raises(StaleCaseVersionError) as error:
        transition_case(
            make_case(),
            expected_version=2,
            to_state=CaseState.DIAGNOSING,
            actor="WORKER",
            reason_code="DIAGNOSIS_STARTED",
            correlation_id="correlation_001",
            policy_version="policy-v1",
            occurred_at=NOW,
        )

    assert error.value.code == "STALE_CASE_VERSION"


def test_transition_time_cannot_regress() -> None:
    with pytest.raises(CaseTransitionError) as error:
        transition_case(
            make_case(),
            expected_version=1,
            to_state=CaseState.DIAGNOSING,
            actor="WORKER",
            reason_code="DIAGNOSIS_STARTED",
            correlation_id="correlation_001",
            policy_version="policy-v1",
            occurred_at=NOW - timedelta(seconds=1),
        )

    assert error.value.code == "TRANSITION_TIME_REGRESSION"


def test_terminal_transition_requires_reason_and_clears_wake_time() -> None:
    case = replace(
        make_case(CaseState.POLICY_CHECK),
        next_evaluation_at=NOW + timedelta(hours=1),
    )
    with pytest.raises(CaseTransitionError) as error:
        transition_case(
            case,
            expected_version=1,
            to_state=CaseState.STOPPED,
            actor="WORKER",
            reason_code="ALREADY_PAID",
            correlation_id="correlation_001",
            policy_version="policy-v1",
            occurred_at=NOW,
        )
    assert error.value.code == "TERMINAL_REASON_REQUIRED"

    updated, _ = transition_case(
        case,
        expected_version=1,
        to_state=CaseState.STOPPED,
        actor="WORKER",
        reason_code="ALREADY_PAID",
        correlation_id="correlation_001",
        policy_version="policy-v1",
        occurred_at=NOW,
        terminal_reason="ALREADY_PAID",
    )
    assert updated.terminal_reason == "ALREADY_PAID"
    assert updated.next_evaluation_at is None


def test_recovered_transition_requires_authoritative_evidence() -> None:
    with pytest.raises(CaseTransitionError) as error:
        transition_case(
            make_case(CaseState.VERIFYING),
            expected_version=1,
            to_state=CaseState.RECOVERED,
            actor="OUTCOME_VERIFIER",
            reason_code="PAYMENT_CONFIRMED",
            correlation_id="correlation_001",
            policy_version="policy-v1",
            occurred_at=NOW,
            terminal_reason="PAYMENT_CONFIRMED",
        )
    assert error.value.code == "AUTHORITATIVE_EVIDENCE_REQUIRED"


def test_recovery_case_serialization_is_json_contract_shaped() -> None:
    value = make_case().to_dict()

    assert value["schema_version"] == "1.0"
    assert value["workflow_type"] == "PAYMENT_DEGRADATION"
    assert value["subject_type"] == "PAYMENT"
    assert value["state"] == "DETECTED"
    assert value["created_at"] == "2026-08-25T12:00:00Z"
    assert value["updated_at"] == "2026-08-25T12:00:00Z"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"currency": "inr"}, "currency"),
        ({"state_version": 0}, "state_version"),
        ({"retry_count": -1}, "retry_count"),
        ({"revenue_at_risk_minor": True}, "integer"),
        ({"updated_at": NOW - timedelta(seconds=1)}, "precede"),
        ({"terminal_reason": "NOT_TERMINAL"}, "nonterminal"),
    ],
)
def test_recovery_case_rejects_invalid_values(changes: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        replace(make_case(), **changes)
