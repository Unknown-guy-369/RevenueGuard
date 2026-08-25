from __future__ import annotations

from datetime import UTC, datetime

import pytest
from revenueguard_domain import (
    ActionType,
    EventSource,
    NormalizedFailureCategory,
    RevenueRiskEvent,
    SubjectType,
    WorkflowType,
    diagnose_event,
    select_case_identity,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


def make_event(
    category: NormalizedFailureCategory,
    *,
    payment_id: str | None = "payment_001",
    subscription_id: str | None = None,
    invoice_id: str | None = None,
    payment_link_id: str | None = None,
) -> RevenueRiskEvent:
    return RevenueRiskEvent(
        event_id="event_001",
        merchant_id="merchant_001",
        source=EventSource.RAZORPAY,
        source_event_id="provider_event_001",
        event_type="payment.failed",
        occurred_at=NOW,
        received_at=NOW,
        amount_minor=10_001,
        currency="INR",
        normalized_failure_category=category,
        correlation_id="correlation_001",
        source_payload_reference="webhook:event_001",
        customer_id="customer_001",
        payment_id=payment_id,
        subscription_id=subscription_id,
        invoice_id=invoice_id,
        payment_link_id=payment_link_id,
    )


@pytest.mark.parametrize(
    ("category", "code", "first_action"),
    [
        (
            NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            "TEMPORARY_INSUFFICIENT_FUNDS",
            ActionType.DEFER_RETRY,
        ),
        (
            NormalizedFailureCategory.EXPIRED_PAYMENT_METHOD,
            "EXPIRED_PAYMENT_METHOD",
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
        ),
        (
            NormalizedFailureCategory.AUTHENTICATION_FAILURE,
            "PAYMENT_AUTHENTICATION_REQUIRED",
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
        ),
        (
            NormalizedFailureCategory.CUSTOMER_ACTION_REQUIRED,
            "CUSTOMER_ACTION_REQUIRED",
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
        ),
        (
            NormalizedFailureCategory.ISSUER_UNAVAILABLE,
            "ISSUER_TEMPORARILY_UNAVAILABLE",
            ActionType.DEFER_RETRY,
        ),
        (
            NormalizedFailureCategory.GATEWAY_UNAVAILABLE,
            "GATEWAY_TEMPORARILY_UNAVAILABLE",
            ActionType.PAUSE_RETRIES,
        ),
        (
            NormalizedFailureCategory.UNKNOWN,
            "UNKNOWN_PAYMENT_FAILURE",
            ActionType.ESCALATE_HUMAN,
        ),
    ],
)
def test_failure_diagnosis_is_stable_and_ends_with_no_action(
    category: NormalizedFailureCategory, code: str, first_action: ActionType
) -> None:
    first = diagnose_event(make_event(category))
    second = diagnose_event(make_event(category))

    assert first == second
    assert first is not None
    assert first.code == code
    assert first.candidates[0].action_type is first_action
    assert first.candidates[-1].action_type is ActionType.NO_ACTION
    assert tuple(candidate.rank for candidate in first.candidates) == tuple(
        range(1, len(first.candidates) + 1)
    )


def test_expected_recovery_uses_deterministic_integer_arithmetic() -> None:
    diagnosis = diagnose_event(make_event(NormalizedFailureCategory.INSUFFICIENT_FUNDS))

    assert diagnosis is not None
    assert diagnosis.candidates[0].expected_net_recovery_minor == 7_000
    assert diagnosis.confidence_basis_points == 9_000
    assert diagnosis.defer_until is not None


def test_none_category_does_not_diagnose() -> None:
    assert diagnose_event(make_event(NormalizedFailureCategory.NONE)) is None


def test_dispute_is_terminal_and_stops_before_no_action() -> None:
    diagnosis = diagnose_event(make_event(NormalizedFailureCategory.DISPUTE))

    assert diagnosis is not None
    assert diagnosis.terminal is True
    assert diagnosis.code == "PAYMENT_DISPUTED"
    assert [candidate.action_type for candidate in diagnosis.candidates] == [
        ActionType.STOP_AUTOMATION,
        ActionType.NO_ACTION,
    ]


def test_case_identity_prefers_subscription_then_invoice_then_payment() -> None:
    subscription = select_case_identity(
        make_event(
            NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            subscription_id="subscription_001",
            invoice_id="invoice_001",
        )
    )
    invoice = select_case_identity(
        make_event(
            NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            payment_id=None,
            invoice_id="invoice_001",
        )
    )
    payment = select_case_identity(make_event(NormalizedFailureCategory.INSUFFICIENT_FUNDS))

    assert subscription is not None
    assert subscription.subject_type is SubjectType.SUBSCRIPTION
    assert subscription.workflow_type is WorkflowType.FAILED_SUBSCRIPTION
    assert invoice is not None
    assert invoice.subject_type is SubjectType.INVOICE
    assert invoice.workflow_type is WorkflowType.B2B_PROMISE_TO_PAY
    assert payment is not None
    assert payment.subject_type is SubjectType.PAYMENT
    assert payment.workflow_type is WorkflowType.PAYMENT_DEGRADATION


def test_episode_key_is_stable_and_changes_with_reliable_reference() -> None:
    first = select_case_identity(
        make_event(
            NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            subscription_id="subscription_001",
        )
    )
    replay = select_case_identity(
        make_event(
            NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            subscription_id="subscription_001",
        )
    )
    next_episode = select_case_identity(
        make_event(
            NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            payment_id="payment_002",
            subscription_id="subscription_001",
        )
    )

    assert first is not None and replay is not None and next_episode is not None
    assert first.episode_key == replay.episode_key
    assert first.episode_key != next_episode.episode_key
    assert len(first.episode_key or "") == 64


def test_payment_link_only_event_is_not_assigned_an_invented_subject() -> None:
    event = make_event(
        NormalizedFailureCategory.UNKNOWN,
        payment_id=None,
        payment_link_id="payment_link_001",
    )

    assert select_case_identity(event) is None
