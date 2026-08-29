from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from revenueguard_domain import (
    ActionFingerprintInput,
    ActionType,
    CandidateAction,
    CaseState,
    ConsentState,
    ContactChannel,
    HumanReviewDecision,
    HumanReviewRequest,
    IncidentConstraint,
    IncidentScope,
    MerchantPolicySnapshot,
    PolicyEvaluationInput,
    PolicyResult,
    ReviewDecisionType,
    VersionBundle,
    decide_review,
    evaluate_policy,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
ALL_ACTIONS = frozenset(ActionType)


def candidate(
    action_type: ActionType,
    rank: int = 1,
    *,
    expected: int = 5_000,
    channel: ContactChannel | None = None,
) -> CandidateAction:
    return CandidateAction(
        action_type=action_type,
        recovery_probability_basis_points=7_500,
        expected_net_recovery_minor=expected,
        rank=rank,
        target="customer_001",
        logical_attempt=1,
        channel=channel,
    )


def no_action(rank: int = 2) -> CandidateAction:
    return candidate(ActionType.NO_ACTION, rank, expected=0)


def make_policy(**changes: object) -> MerchantPolicySnapshot:
    values: dict[str, object] = {
        "version": "policy-v1",
        "effective_at": NOW - timedelta(days=1),
        "allowed_actions": ALL_ACTIONS,
        "retry_limit": 3,
        "contact_limit": 2,
        "minimum_expected_net_recovery_minor": 100,
        "human_review_amount_minor": 50_000,
        "minimum_confidence_basis_points": 5_000,
        "default_defer_seconds": 3_600,
        "timezone": "UTC",
        "quiet_hours_start": time(22),
        "quiet_hours_end": time(7),
    }
    values.update(changes)
    return MerchantPolicySnapshot(**values)  # type: ignore[arg-type]


def make_evaluation(
    *candidates: CandidateAction,
    evaluated_at: datetime = NOW,
    **changes: object,
) -> PolicyEvaluationInput:
    values: dict[str, object] = {
        "case_id": "case_001",
        "amount_minor": 10_000,
        "currency": "INR",
        "confidence_basis_points": 9_000,
        "retry_count": 0,
        "contact_count": 0,
        "evaluated_at": evaluated_at,
        "candidates": candidates or (candidate(ActionType.DEFER_RETRY), no_action()),
        "evidence_references": ("event_001",),
    }
    values.update(changes)
    return PolicyEvaluationInput(**values)  # type: ignore[arg-type]


def approved_request(
    policy: MerchantPolicySnapshot,
    selected: CandidateAction,
) -> HumanReviewRequest:
    fingerprint = ActionFingerprintInput(
        case_id="case_001",
        action_type=selected.action_type.value,
        target=selected.target,
        amount_minor=10_000,
        currency="INR",
        logical_attempt=selected.logical_attempt,
        policy_digest=policy.content_digest,
    ).digest()
    requested = HumanReviewRequest(
        review_id="review_001",
        merchant_id="merchant_001",
        case_id="case_001",
        action_fingerprint=fingerprint,
        proposed_action_type=selected.action_type.value,
        evidence_references=("event_001",),
        policy_version=policy.version,
        policy_digest=policy.content_digest,
        reason_code="HUMAN_REVIEW_REQUIRED",
        risk_detail="low confidence",
        requested_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
    )
    return decide_review(
        requested,
        HumanReviewDecision(
            review_id=requested.review_id,
            decision=ReviewDecisionType.APPROVE,
            reviewer_id="operator_001",
            rationale="approved",
            decided_at=NOW - timedelta(minutes=30),
        ),
    )


def test_policy_snapshot_digest_is_stable_and_content_bound() -> None:
    first = make_policy()
    second = make_policy()
    changed = make_policy(retry_limit=4)

    assert first.content_digest == second.content_digest
    assert first.content_digest != changed.content_digest
    assert len(first.content_digest) == 64
    assert first.canonical_document()["allowed_actions"] == sorted(
        action.value for action in ActionType
    )


def test_proceed_selects_first_authorized_candidate() -> None:
    decision = evaluate_policy(make_policy(), make_evaluation())

    assert decision.result is PolicyResult.PROCEED
    assert decision.selected_action.action_type is ActionType.DEFER_RETRY
    assert decision.reason_codes == ("POLICY_AUTHORIZED",)
    assert decision.resulting_state is CaseState.READY
    assert decision.next_evaluation_at is None


def test_candidate_skip_falls_through_and_retains_ordered_reasons() -> None:
    retry = candidate(ActionType.DEFER_RETRY)
    escalation = candidate(ActionType.ESCALATE_HUMAN, 2, expected=0)
    policy = make_policy(allowed_actions=frozenset({ActionType.SEND_REMINDER}))

    decision = evaluate_policy(policy, make_evaluation(retry, escalation, no_action(3)))

    assert decision.result is PolicyResult.REQUIRE_HUMAN
    assert decision.selected_action is escalation
    assert decision.reason_codes == ("ACTION_NOT_ALLOWED", "AGENT_ESCALATION_REQUESTED")
    assert decision.evaluated_candidates == (retry, escalation)


def test_all_operational_candidates_skipped_selects_no_action_without_wake() -> None:
    retry = candidate(ActionType.DEFER_RETRY)
    decision = evaluate_policy(
        make_policy(allowed_actions=frozenset({ActionType.SEND_REMINDER})),
        make_evaluation(retry, no_action()),
    )

    assert decision.result is PolicyResult.SKIP
    assert decision.selected_action.action_type is ActionType.NO_ACTION
    assert decision.reason_codes == ("ACTION_NOT_ALLOWED", "NO_ELIGIBLE_ACTION")
    assert decision.resulting_state is CaseState.DECISION_PENDING
    assert decision.next_evaluation_at is None


@pytest.mark.parametrize(
    ("flag", "reason"),
    [
        ("terminal", "CASE_ALREADY_TERMINAL"),
        ("already_paid", "ALREADY_PAID"),
        ("disputed", "PAYMENT_DISPUTED"),
        ("cancelled", "SUBJECT_CANCELLED"),
    ],
)
def test_global_truth_stops_before_candidate_guardians(flag: str, reason: str) -> None:
    decision = evaluate_policy(make_policy(), make_evaluation(**{flag: True}))

    assert decision.result is PolicyResult.STOP
    assert decision.selected_action.action_type is ActionType.STOP_AUTOMATION
    assert decision.reason_codes == (reason,)
    assert decision.resulting_state is CaseState.STOPPED


def test_retry_and_contact_ceilings_only_apply_to_matching_action_classes() -> None:
    retry = evaluate_policy(make_policy(), make_evaluation(retry_count=3))
    reminder = candidate(ActionType.SEND_REMINDER, channel=ContactChannel.EMAIL)
    contact = evaluate_policy(
        make_policy(),
        make_evaluation(
            reminder,
            no_action(),
            contact_count=2,
            consent_by_channel=((ContactChannel.EMAIL, ConsentState.GRANTED),),
        ),
    )
    escalation = evaluate_policy(
        make_policy(),
        make_evaluation(
            candidate(ActionType.ESCALATE_HUMAN, expected=0),
            no_action(),
            retry_count=100,
            contact_count=100,
        ),
    )

    assert retry.reason_codes[0] == "RETRY_LIMIT_REACHED"
    assert contact.reason_codes[0] == "CONTACT_LIMIT_REACHED"
    assert escalation.result is PolicyResult.REQUIRE_HUMAN


@pytest.mark.parametrize(
    ("consent", "opted_out", "reason"),
    [
        (ConsentState.UNKNOWN, frozenset(), "CHANNEL_CONSENT_NOT_GRANTED"),
        (ConsentState.DENIED, frozenset(), "CHANNEL_CONSENT_NOT_GRANTED"),
        (ConsentState.GRANTED, frozenset({ContactChannel.EMAIL}), "CHANNEL_OPTED_OUT"),
    ],
)
def test_contact_requires_consent_and_honors_opt_out(
    consent: ConsentState, opted_out: frozenset[ContactChannel], reason: str
) -> None:
    reminder = candidate(ActionType.SEND_REMINDER, channel=ContactChannel.EMAIL)
    decision = evaluate_policy(
        make_policy(),
        make_evaluation(
            reminder,
            no_action(),
            consent_by_channel=((ContactChannel.EMAIL, consent),),
            opted_out_channels=opted_out,
        ),
    )

    assert decision.result is PolicyResult.SKIP
    assert decision.reason_codes[0] == reason


@pytest.mark.parametrize(
    ("scope", "action_type", "channel", "applies"),
    [
        (IncidentScope.PAYMENT_RAIL, ActionType.DEFER_RETRY, None, True),
        (IncidentScope.GATEWAY, ActionType.CREATE_PAYMENT_LINK, None, True),
        (IncidentScope.ISSUER, ActionType.SEND_REMINDER, ContactChannel.EMAIL, False),
        (IncidentScope.CONTACT_CHANNEL, ActionType.SEND_REMINDER, ContactChannel.EMAIL, True),
        (IncidentScope.CONTACT_CHANNEL, ActionType.SEND_REMINDER, ContactChannel.SMS, False),
        (IncidentScope.ALL_AUTOMATION, ActionType.PAUSE_RETRIES, None, True),
        (IncidentScope.ALL_AUTOMATION, ActionType.ESCALATE_HUMAN, None, False),
        (IncidentScope.ALL_AUTOMATION, ActionType.STOP_AUTOMATION, None, False),
        (IncidentScope.ALL_AUTOMATION, ActionType.NO_ACTION, None, False),
    ],
)
def test_incident_applicability_matrix(
    scope: IncidentScope,
    action_type: ActionType,
    channel: ContactChannel | None,
    applies: bool,
) -> None:
    incident = IncidentConstraint(
        incident_id="incident_001",
        scope=scope,
        channel=ContactChannel.EMAIL if scope is IncidentScope.CONTACT_CHANNEL else None,
        starts_at=NOW - timedelta(minutes=1),
        ends_at=NOW + timedelta(minutes=30),
    )

    assert incident.applies(candidate(action_type, channel=channel), NOW) is applies


def test_matching_incident_defers_until_earliest_end() -> None:
    action = candidate(ActionType.DEFER_RETRY)
    incidents = (
        IncidentConstraint(
            incident_id="incident_later",
            scope=IncidentScope.GATEWAY,
            starts_at=NOW - timedelta(minutes=1),
            ends_at=NOW + timedelta(hours=2),
        ),
        IncidentConstraint(
            incident_id="incident_earlier",
            scope=IncidentScope.PAYMENT_RAIL,
            starts_at=NOW - timedelta(minutes=1),
            ends_at=NOW + timedelta(hours=1),
        ),
    )

    decision = evaluate_policy(
        make_policy(), make_evaluation(action, no_action(), incidents=incidents)
    )

    assert decision.result is PolicyResult.DEFER
    assert decision.reason_codes == ("ACTIVE_INCIDENT",)
    assert decision.resulting_state is CaseState.DEFERRED
    assert decision.next_evaluation_at == NOW + timedelta(hours=1)


def test_quiet_hours_only_defer_customer_contact() -> None:
    local_quiet_time = datetime(2026, 8, 25, 23, tzinfo=UTC)
    reminder = candidate(ActionType.SEND_REMINDER, channel=ContactChannel.EMAIL)
    contact = evaluate_policy(
        make_policy(),
        make_evaluation(
            reminder,
            no_action(),
            evaluated_at=local_quiet_time,
            consent_by_channel=((ContactChannel.EMAIL, ConsentState.GRANTED),),
        ),
    )
    retry = evaluate_policy(make_policy(), make_evaluation(evaluated_at=local_quiet_time))

    assert contact.result is PolicyResult.DEFER
    assert contact.reason_codes == ("QUIET_HOURS",)
    assert contact.next_evaluation_at == datetime(2026, 8, 26, 7, tzinfo=UTC)
    assert retry.result is PolicyResult.PROCEED


def test_expected_value_guard_skips_operational_but_not_safe_internal_candidates() -> None:
    policy = make_policy(minimum_expected_net_recovery_minor=10_000)
    operational = evaluate_policy(
        policy,
        make_evaluation(candidate(ActionType.DEFER_RETRY, expected=0), no_action()),
    )
    escalation = evaluate_policy(
        policy,
        make_evaluation(candidate(ActionType.ESCALATE_HUMAN, expected=0), no_action()),
    )
    pause = evaluate_policy(
        policy,
        make_evaluation(candidate(ActionType.PAUSE_RETRIES, expected=0), no_action()),
    )

    assert operational.reason_codes[0] == "EXPECTED_VALUE_BELOW_MINIMUM"
    assert escalation.result is PolicyResult.REQUIRE_HUMAN
    assert pause.result is PolicyResult.PROCEED


def test_internal_pause_still_requires_declared_capability() -> None:
    pause = candidate(ActionType.PAUSE_RETRIES, expected=0)
    decision = evaluate_policy(
        make_policy(allowed_actions=frozenset({ActionType.DEFER_RETRY})),
        make_evaluation(pause, no_action()),
    )

    assert decision.result is PolicyResult.SKIP
    assert decision.reason_codes[0] == "ACTION_NOT_ALLOWED"


def test_high_amount_or_low_confidence_money_intent_requires_human_review() -> None:
    decision = evaluate_policy(
        make_policy(),
        make_evaluation(
            candidate(ActionType.CREATE_PAYMENT_LINK),
            no_action(),
            amount_minor=50_000,
            confidence_basis_points=4_999,
        ),
    )

    assert decision.result is PolicyResult.REQUIRE_HUMAN
    assert decision.resulting_state is CaseState.ESCALATED
    assert decision.reason_codes == ("HUMAN_REVIEW_REQUIRED",)


def test_matching_approval_only_clears_human_gate() -> None:
    policy = make_policy(minimum_confidence_basis_points=9_500)
    selected = candidate(ActionType.CREATE_PAYMENT_LINK)
    approval = approved_request(policy, selected)

    authorized = evaluate_policy(
        policy,
        make_evaluation(selected, no_action(), approval=approval),
    )
    incident = IncidentConstraint(
        incident_id="incident_001",
        scope=IncidentScope.GATEWAY,
        starts_at=NOW - timedelta(minutes=1),
        ends_at=NOW + timedelta(minutes=30),
    )
    still_deferred = evaluate_policy(
        policy,
        make_evaluation(selected, no_action(), approval=approval, incidents=(incident,)),
    )
    still_stopped = evaluate_policy(
        policy,
        make_evaluation(selected, no_action(), approval=approval, already_paid=True),
    )

    assert authorized.result is PolicyResult.PROCEED
    assert still_deferred.result is PolicyResult.DEFER
    assert still_stopped.result is PolicyResult.STOP


def test_changed_policy_digest_invalidates_prior_approval() -> None:
    old_policy = make_policy(minimum_confidence_basis_points=9_500)
    selected = candidate(ActionType.CREATE_PAYMENT_LINK)
    approval = approved_request(old_policy, selected)
    new_policy = make_policy(
        version="policy-v2",
        retry_limit=4,
        minimum_confidence_basis_points=9_500,
    )

    decision = evaluate_policy(
        new_policy,
        make_evaluation(selected, no_action(), approval=approval),
    )

    assert decision.result is PolicyResult.REQUIRE_HUMAN


def test_low_confidence_retry_is_deferred_before_human_review() -> None:
    decision = evaluate_policy(
        make_policy(),
        make_evaluation(
            confidence_basis_points=4_999,
            diagnosis_defer_until=NOW + timedelta(minutes=15),
        ),
    )

    assert decision.result is PolicyResult.DEFER
    assert decision.selected_action.action_type is ActionType.DEFER_RETRY
    assert decision.reason_codes == ("LOW_CONFIDENCE_RETRY_DEFERRED",)
    assert decision.next_evaluation_at == NOW + timedelta(minutes=15)


def test_explicit_agent_escalation_creates_a_human_gate() -> None:
    decision = evaluate_policy(
        make_policy(),
        make_evaluation(candidate(ActionType.ESCALATE_HUMAN, expected=0), no_action()),
    )

    assert decision.result is PolicyResult.REQUIRE_HUMAN
    assert decision.resulting_state is CaseState.ESCALATED
    assert decision.reason_codes == ("AGENT_ESCALATION_REQUESTED",)


def test_version_bundle_uses_exact_non_model_placeholders() -> None:
    versions = VersionBundle(policy="digest", features="features-v1", application="app-v1")

    assert versions.to_dict()["model"] == "NOT_APPLICABLE"
    assert versions.to_dict()["prompt"] == "NOT_APPLICABLE"


def test_candidate_ranks_must_be_in_deterministic_input_order() -> None:
    with pytest.raises(ValueError, match="ordered and contiguous"):
        make_evaluation(no_action(2), candidate(ActionType.DEFER_RETRY, rank=1))


def test_future_policy_cannot_evaluate_past_evidence() -> None:
    with pytest.raises(ValueError, match="not yet effective"):
        evaluate_policy(
            make_policy(effective_at=NOW + timedelta(seconds=1)),
            make_evaluation(),
        )


def test_policy_currency_mismatch_stops_before_money_comparison() -> None:
    decision = evaluate_policy(make_policy(currency="INR"), make_evaluation(currency="USD"))

    assert decision.result is PolicyResult.STOP
    assert decision.reason_codes == ("POLICY_CURRENCY_MISMATCH",)


def test_unknown_equivalent_action_and_cross_workflow_contact_defer() -> None:
    unknown = evaluate_policy(
        make_policy(),
        make_evaluation(unknown_equivalent_action=True),
    )
    reminder = candidate(ActionType.SEND_REMINDER, channel=ContactChannel.EMAIL)
    contact = evaluate_policy(
        make_policy(),
        make_evaluation(
            reminder,
            no_action(),
            customer_contact_in_progress=True,
            consent_by_channel=((ContactChannel.EMAIL, ConsentState.GRANTED),),
        ),
    )

    assert unknown.result is PolicyResult.DEFER
    assert unknown.reason_codes == ("EQUIVALENT_ACTION_OUTCOME_UNKNOWN",)
    assert contact.result is PolicyResult.DEFER
    assert contact.reason_codes == ("CUSTOMER_CONTACT_ALREADY_IN_PROGRESS",)


def test_retry_and_contact_ceiling_properties_hold_for_counts_around_limits() -> None:
    policy = make_policy(retry_limit=3, contact_limit=2)
    reminder = candidate(ActionType.SEND_REMINDER, channel=ContactChannel.EMAIL)
    for retry_count in range(0, 8):
        decision = evaluate_policy(policy, make_evaluation(retry_count=retry_count))
        assert (decision.result is PolicyResult.PROCEED) is (retry_count < 3)
    for contact_count in range(0, 8):
        decision = evaluate_policy(
            policy,
            make_evaluation(
                reminder,
                no_action(),
                contact_count=contact_count,
                consent_by_channel=((ContactChannel.EMAIL, ConsentState.GRANTED),),
            ),
        )
        assert (decision.result is PolicyResult.PROCEED) is (contact_count < 2)


def test_candidate_receipt_shape_preserves_action_identity() -> None:
    selected = candidate(ActionType.SEND_REMINDER, channel=ContactChannel.EMAIL)

    assert selected.to_dict() == {
        "action_type": "SEND_REMINDER",
        "recovery_probability": 0.75,
        "expected_net_recovery_minor": 5_000,
        "rank": 1,
        "target": "customer_001",
        "logical_attempt": 1,
        "channel": "EMAIL",
    }
