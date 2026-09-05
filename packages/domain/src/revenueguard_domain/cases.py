"""Recovery-case state and transition invariants."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

SCHEMA_VERSION: Final = "1.0"
_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")


class WorkflowType(StrEnum):
    FAILED_SUBSCRIPTION = "FAILED_SUBSCRIPTION"
    PAYMENT_DEGRADATION = "PAYMENT_DEGRADATION"
    B2B_PROMISE_TO_PAY = "B2B_PROMISE_TO_PAY"


class SubjectType(StrEnum):
    PAYMENT = "PAYMENT"
    SUBSCRIPTION = "SUBSCRIPTION"
    INVOICE = "INVOICE"
    PORTFOLIO_INCIDENT = "PORTFOLIO_INCIDENT"


class CaseState(StrEnum):
    DETECTED = "DETECTED"
    DIAGNOSING = "DIAGNOSING"
    DECISION_PENDING = "DECISION_PENDING"
    POLICY_CHECK = "POLICY_CHECK"
    READY = "READY"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    UNKNOWN = "UNKNOWN"
    DEFERRED = "DEFERRED"
    ESCALATED = "ESCALATED"
    RECOVERED = "RECOVERED"
    STOPPED = "STOPPED"


TERMINAL_STATES: Final = frozenset({CaseState.RECOVERED, CaseState.STOPPED})
ALLOWED_TRANSITIONS: Final[Mapping[CaseState, frozenset[CaseState]]] = MappingProxyType(
    {
        CaseState.DETECTED: frozenset({CaseState.DIAGNOSING}),
        CaseState.DIAGNOSING: frozenset({CaseState.DECISION_PENDING}),
        CaseState.DECISION_PENDING: frozenset({CaseState.POLICY_CHECK}),
        CaseState.POLICY_CHECK: frozenset(
            {
                CaseState.READY,
                CaseState.DEFERRED,
                CaseState.DECISION_PENDING,
                CaseState.ESCALATED,
                CaseState.STOPPED,
            }
        ),
        CaseState.READY: frozenset({CaseState.EXECUTING}),
        CaseState.EXECUTING: frozenset({CaseState.VERIFYING, CaseState.UNKNOWN}),
        CaseState.VERIFYING: frozenset(
            {
                CaseState.RECOVERED,
                CaseState.DECISION_PENDING,
                CaseState.UNKNOWN,
                CaseState.STOPPED,
            }
        ),
        CaseState.UNKNOWN: frozenset({CaseState.VERIFYING, CaseState.ESCALATED}),
        CaseState.DEFERRED: frozenset({CaseState.DECISION_PENDING}),
        CaseState.ESCALATED: frozenset({CaseState.DECISION_PENDING, CaseState.STOPPED}),
        CaseState.RECOVERED: frozenset(),
        CaseState.STOPPED: frozenset(),
    }
)


class CaseTransitionError(ValueError):
    """A requested transition violates the frozen state machine."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class StaleCaseVersionError(CaseTransitionError):
    """A caller evaluated an older case version."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryCase:
    case_id: str
    merchant_id: str
    workflow_type: WorkflowType
    subject_type: SubjectType
    subject_id: str
    revenue_at_risk_minor: int
    currency: str
    state: CaseState
    state_version: int
    retry_count: int
    contact_count: int
    created_at: datetime
    updated_at: datetime
    customer_id: str | None = None
    diagnosis: str | None = None
    diagnosis_confidence: float | None = None
    active_incident_id: str | None = None
    next_evaluation_at: datetime | None = None
    terminal_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported RecoveryCase schema")
        for required_name, required_value, required_maximum in (
            ("case_id", self.case_id, 128),
            ("merchant_id", self.merchant_id, 128),
            ("subject_id", self.subject_id, 128),
        ):
            _identifier(required_name, required_value, required_maximum)
        for optional_name, optional_value, optional_maximum in (
            ("customer_id", self.customer_id, 128),
            ("diagnosis", self.diagnosis, 128),
            ("active_incident_id", self.active_incident_id, 128),
            ("terminal_reason", self.terminal_reason, 256),
        ):
            if optional_value is not None:
                _identifier(optional_name, optional_value, optional_maximum)
        if not isinstance(self.workflow_type, WorkflowType):
            raise TypeError("workflow_type must be a WorkflowType")
        if not isinstance(self.subject_type, SubjectType):
            raise TypeError("subject_type must be a SubjectType")
        if not isinstance(self.state, CaseState):
            raise TypeError("state must be a CaseState")
        for name, value, minimum in (
            ("revenue_at_risk_minor", self.revenue_at_risk_minor, 0),
            ("state_version", self.state_version, 1),
            ("retry_count", self.retry_count, 0),
            ("contact_count", self.contact_count, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase ISO code")
        if self.diagnosis_confidence is not None:
            if isinstance(self.diagnosis_confidence, bool) or not isinstance(
                self.diagnosis_confidence, (int, float)
            ):
                raise TypeError("diagnosis_confidence must be numeric")
            if not 0 <= self.diagnosis_confidence <= 1:
                raise ValueError("diagnosis_confidence must be between zero and one")
        if self.state in TERMINAL_STATES and self.terminal_reason is None:
            raise ValueError("terminal cases require terminal_reason")
        if self.state not in TERMINAL_STATES and self.terminal_reason is not None:
            raise ValueError("nonterminal cases cannot have terminal_reason")
        object.__setattr__(self, "created_at", _utc("created_at", self.created_at))
        object.__setattr__(self, "updated_at", _utc("updated_at", self.updated_at))
        if self.next_evaluation_at is not None:
            object.__setattr__(
                self, "next_evaluation_at", _utc("next_evaluation_at", self.next_evaluation_at)
            )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "workflow_type": self.workflow_type.value,
            "subject_type": self.subject_type.value,
            "subject_id": self.subject_id,
            "customer_id": self.customer_id,
            "revenue_at_risk_minor": self.revenue_at_risk_minor,
            "currency": self.currency,
            "state": self.state.value,
            "state_version": self.state_version,
            "diagnosis": self.diagnosis,
            "diagnosis_confidence": self.diagnosis_confidence,
            "retry_count": self.retry_count,
            "contact_count": self.contact_count,
            "active_incident_id": self.active_incident_id,
            "next_evaluation_at": _format(self.next_evaluation_at),
            "terminal_reason": self.terminal_reason,
            "created_at": _format(self.created_at),
            "updated_at": _format(self.updated_at),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseTransition:
    case_id: str
    merchant_id: str
    before_state: CaseState
    after_state: CaseState
    before_version: int
    after_version: int
    actor: str
    reason_code: str
    reason_detail: str | None
    correlation_id: str
    policy_version: str
    authoritative_evidence_reference: str | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("case_id", self.case_id, 128),
            ("merchant_id", self.merchant_id, 128),
            ("actor", self.actor, 128),
            ("reason_code", self.reason_code, 128),
            ("correlation_id", self.correlation_id, 128),
            ("policy_version", self.policy_version, 128),
        ):
            _identifier(name, value, maximum)
        if self.reason_detail is not None and len(self.reason_detail) > 2000:
            raise ValueError("reason_detail cannot exceed 2000 characters")
        if self.after_version != self.before_version + 1:
            raise ValueError("after_version must equal before_version + 1")
        if self.after_state not in ALLOWED_TRANSITIONS[self.before_state]:
            raise ValueError("transition is not allowed")
        if self.after_state is CaseState.RECOVERED and not self.authoritative_evidence_reference:
            raise ValueError("recovered transition requires authoritative evidence")
        if (
            self.authoritative_evidence_reference is not None
            and len(self.authoritative_evidence_reference) > 512
        ):
            raise ValueError("authoritative evidence reference cannot exceed 512 characters")
        object.__setattr__(self, "occurred_at", _utc("occurred_at", self.occurred_at))


def can_transition(before: CaseState, after: CaseState) -> bool:
    return after in ALLOWED_TRANSITIONS[before]


def transition_case(
    case: RecoveryCase,
    *,
    expected_version: int,
    to_state: CaseState,
    actor: str,
    reason_code: str,
    correlation_id: str,
    occurred_at: datetime,
    reason_detail: str | None = None,
    policy_version: str,
    authoritative_evidence_reference: str | None = None,
    terminal_reason: str | None = None,
    next_evaluation_at: datetime | None = None,
) -> tuple[RecoveryCase, CaseTransition]:
    if case.state_version != expected_version:
        raise StaleCaseVersionError(
            "STALE_CASE_VERSION",
            f"expected version {expected_version}, found {case.state_version}",
        )
    if not can_transition(case.state, to_state):
        raise CaseTransitionError(
            "ILLEGAL_CASE_TRANSITION", f"cannot transition {case.state.value} to {to_state.value}"
        )
    transitioned_at = _utc("occurred_at", occurred_at)
    if transitioned_at < case.updated_at:
        raise CaseTransitionError(
            "TRANSITION_TIME_REGRESSION", "transition time precedes case update"
        )
    if to_state in TERMINAL_STATES and not terminal_reason:
        raise CaseTransitionError(
            "TERMINAL_REASON_REQUIRED", "terminal transition requires a reason"
        )
    if to_state not in TERMINAL_STATES and terminal_reason is not None:
        raise CaseTransitionError(
            "UNEXPECTED_TERMINAL_REASON", "nonterminal transition has a reason"
        )
    if to_state is CaseState.RECOVERED and not authoritative_evidence_reference:
        raise CaseTransitionError(
            "AUTHORITATIVE_EVIDENCE_REQUIRED",
            "recovered transition requires authoritative evidence",
        )
    updated = replace(
        case,
        state=to_state,
        state_version=case.state_version + 1,
        updated_at=transitioned_at,
        terminal_reason=terminal_reason,
        next_evaluation_at=next_evaluation_at,
    )
    transition = CaseTransition(
        case_id=case.case_id,
        merchant_id=case.merchant_id,
        before_state=case.state,
        after_state=to_state,
        before_version=case.state_version,
        after_version=updated.state_version,
        actor=actor,
        reason_code=reason_code,
        reason_detail=reason_detail,
        correlation_id=correlation_id,
        policy_version=policy_version,
        authoritative_evidence_reference=authoritative_evidence_reference,
        occurred_at=transitioned_at,
    )
    return updated, transition


def _identifier(name: str, value: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or len(value) > maximum:
        raise ValueError(f"{name} must contain between 1 and {maximum} characters")


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _format(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None
