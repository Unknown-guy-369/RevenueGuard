"""Deterministic diagnosis and recovery candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Final

from revenueguard_domain.cases import SubjectType, WorkflowType
from revenueguard_domain.events import NormalizedFailureCategory, RevenueRiskEvent
from revenueguard_domain.policy import ActionType, CandidateAction, ContactChannel


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseIdentity:
    workflow_type: WorkflowType
    subject_type: SubjectType
    subject_id: str
    episode_key: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnosis:
    code: str
    confidence_basis_points: int
    candidates: tuple[CandidateAction, ...]
    defer_until: datetime | None = None
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class _CandidateTemplate:
    action_type: ActionType
    probability_basis_points: int
    expected_value_basis_points: int
    channel: ContactChannel | None = None


_DIAGNOSES: Final = {
    NormalizedFailureCategory.INSUFFICIENT_FUNDS: (
        "TEMPORARY_INSUFFICIENT_FUNDS",
        9000,
        (
            _CandidateTemplate(ActionType.DEFER_RETRY, 7200, 7000),
            _CandidateTemplate(
                ActionType.REQUEST_PAYMENT_METHOD_UPDATE, 5200, 4800, ContactChannel.EMAIL
            ),
        ),
        86_400,
    ),
    NormalizedFailureCategory.EXPIRED_PAYMENT_METHOD: (
        "EXPIRED_PAYMENT_METHOD",
        9700,
        (
            _CandidateTemplate(
                ActionType.REQUEST_PAYMENT_METHOD_UPDATE, 8000, 7500, ContactChannel.EMAIL
            ),
            _CandidateTemplate(ActionType.CREATE_PAYMENT_LINK, 6200, 5500),
        ),
        0,
    ),
    NormalizedFailureCategory.AUTHENTICATION_FAILURE: (
        "PAYMENT_AUTHENTICATION_REQUIRED",
        9300,
        (
            _CandidateTemplate(
                ActionType.REQUEST_PAYMENT_METHOD_UPDATE, 7200, 6500, ContactChannel.EMAIL
            ),
            _CandidateTemplate(ActionType.CREATE_PAYMENT_LINK, 6100, 5300),
        ),
        0,
    ),
    NormalizedFailureCategory.CUSTOMER_ACTION_REQUIRED: (
        "CUSTOMER_ACTION_REQUIRED",
        9400,
        (
            _CandidateTemplate(
                ActionType.REQUEST_PAYMENT_METHOD_UPDATE, 7600, 6800, ContactChannel.EMAIL
            ),
            _CandidateTemplate(ActionType.SEND_REMINDER, 5600, 4500, ContactChannel.EMAIL),
        ),
        0,
    ),
    NormalizedFailureCategory.ISSUER_UNAVAILABLE: (
        "ISSUER_TEMPORARILY_UNAVAILABLE",
        8800,
        (
            _CandidateTemplate(ActionType.DEFER_RETRY, 7800, 7300),
            _CandidateTemplate(ActionType.PAUSE_RETRIES, 1000, 0),
        ),
        3_600,
    ),
    NormalizedFailureCategory.GATEWAY_UNAVAILABLE: (
        "GATEWAY_TEMPORARILY_UNAVAILABLE",
        9000,
        (
            _CandidateTemplate(ActionType.PAUSE_RETRIES, 1000, 0),
            _CandidateTemplate(ActionType.DEFER_RETRY, 7600, 7100),
        ),
        1_800,
    ),
    NormalizedFailureCategory.UNKNOWN: (
        "UNKNOWN_PAYMENT_FAILURE",
        3000,
        (_CandidateTemplate(ActionType.ESCALATE_HUMAN, 1000, 0),),
        0,
    ),
}


def select_case_identity(event: RevenueRiskEvent) -> CaseIdentity | None:
    if event.subscription_id:
        subject_type = SubjectType.SUBSCRIPTION
        subject_id = event.subscription_id
        workflow = WorkflowType.FAILED_SUBSCRIPTION
    elif event.invoice_id:
        subject_type = SubjectType.INVOICE
        subject_id = event.invoice_id
        workflow = WorkflowType.B2B_PROMISE_TO_PAY
    elif event.payment_id:
        subject_type = SubjectType.PAYMENT
        subject_id = event.payment_id
        workflow = WorkflowType.PAYMENT_DEGRADATION
    else:
        return None
    episode_reference = event.payment_id or event.invoice_id
    episode_key = None
    if episode_reference:
        material = ":".join(
            (
                event.merchant_id,
                workflow.value,
                subject_type.value,
                subject_id,
                episode_reference,
            )
        )
        episode_key = sha256(material.encode()).hexdigest()
    return CaseIdentity(
        workflow_type=workflow,
        subject_type=subject_type,
        subject_id=subject_id,
        episode_key=episode_key,
    )


def diagnose_event(event: RevenueRiskEvent) -> Diagnosis | None:
    category = event.normalized_failure_category
    if category is NormalizedFailureCategory.NONE:
        return None
    if category is NormalizedFailureCategory.DISPUTE:
        return Diagnosis(
            code="PAYMENT_DISPUTED",
            confidence_basis_points=10_000,
            candidates=(
                CandidateAction(
                    action_type=ActionType.STOP_AUTOMATION,
                    recovery_probability_basis_points=0,
                    expected_net_recovery_minor=0,
                    rank=1,
                    target=_target(event),
                ),
                _no_action(event, 2),
            ),
            terminal=True,
        )
    code, confidence, templates, defer_seconds = _DIAGNOSES[category]
    candidates = tuple(
        CandidateAction(
            action_type=template.action_type,
            recovery_probability_basis_points=template.probability_basis_points,
            expected_net_recovery_minor=(
                event.amount_minor * template.expected_value_basis_points // 10_000
            ),
            rank=rank,
            target=_target(event),
            channel=template.channel,
        )
        for rank, template in enumerate(templates, start=1)
    )
    candidates = (*candidates, _no_action(event, len(candidates) + 1))
    return Diagnosis(
        code=code,
        confidence_basis_points=confidence,
        candidates=candidates,
        defer_until=(
            event.occurred_at + timedelta(seconds=defer_seconds) if defer_seconds else None
        ),
    )


def _target(event: RevenueRiskEvent) -> str:
    return (
        event.subscription_id
        or event.invoice_id
        or event.payment_id
        or event.customer_id
        or event.event_id
    )


def _no_action(event: RevenueRiskEvent, rank: int) -> CandidateAction:
    return CandidateAction(
        action_type=ActionType.NO_ACTION,
        recovery_probability_basis_points=0,
        expected_net_recovery_minor=0,
        rank=rank,
        target=_target(event),
    )
