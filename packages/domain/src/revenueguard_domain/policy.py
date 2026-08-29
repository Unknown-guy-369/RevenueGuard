"""Pure, deterministic merchant-policy evaluation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from revenueguard_domain.cases import CaseState
from revenueguard_domain.reviews import HumanReviewRequest

SCHEMA_VERSION: Final = "1.0"
REASON_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ActionType(StrEnum):
    DEFER_RETRY = "DEFER_RETRY"
    CREATE_PAYMENT_LINK = "CREATE_PAYMENT_LINK"
    REQUEST_PAYMENT_METHOD_UPDATE = "REQUEST_PAYMENT_METHOD_UPDATE"
    SEND_REMINDER = "SEND_REMINDER"
    SCHEDULE_PROMISE_REMINDER = "SCHEDULE_PROMISE_REMINDER"
    PAUSE_RETRIES = "PAUSE_RETRIES"
    RESUME_DEFERRED_CASE = "RESUME_DEFERRED_CASE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    STOP_AUTOMATION = "STOP_AUTOMATION"
    NO_ACTION = "NO_ACTION"


class ActionClass(StrEnum):
    RETRY = "RETRY"
    MONEY_INTENT = "MONEY_INTENT"
    CUSTOMER_CONTACT = "CUSTOMER_CONTACT"
    INTERNAL = "INTERNAL"
    ESCALATION = "ESCALATION"
    STOP = "STOP"
    NO_ACTION = "NO_ACTION"


class ContactChannel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"


class ConsentState(StrEnum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    UNKNOWN = "UNKNOWN"


class IncidentScope(StrEnum):
    PAYMENT_RAIL = "PAYMENT_RAIL"
    GATEWAY = "GATEWAY"
    ISSUER = "ISSUER"
    CONTACT_CHANNEL = "CONTACT_CHANNEL"
    ALL_AUTOMATION = "ALL_AUTOMATION"


class PolicyResult(StrEnum):
    PROCEED = "PROCEED"
    DEFER = "DEFER"
    SKIP = "SKIP"
    STOP = "STOP"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


ACTION_CLASSES: Final = {
    ActionType.DEFER_RETRY: ActionClass.RETRY,
    ActionType.CREATE_PAYMENT_LINK: ActionClass.MONEY_INTENT,
    ActionType.REQUEST_PAYMENT_METHOD_UPDATE: ActionClass.CUSTOMER_CONTACT,
    ActionType.SEND_REMINDER: ActionClass.CUSTOMER_CONTACT,
    ActionType.SCHEDULE_PROMISE_REMINDER: ActionClass.CUSTOMER_CONTACT,
    ActionType.PAUSE_RETRIES: ActionClass.INTERNAL,
    ActionType.RESUME_DEFERRED_CASE: ActionClass.INTERNAL,
    ActionType.ESCALATE_HUMAN: ActionClass.ESCALATION,
    ActionType.STOP_AUTOMATION: ActionClass.STOP,
    ActionType.NO_ACTION: ActionClass.NO_ACTION,
}
ALWAYS_ALLOWED_CLASSES: Final = frozenset(
    {ActionClass.ESCALATION, ActionClass.STOP, ActionClass.NO_ACTION}
)
EXPECTED_VALUE_EXEMPT_CLASSES: Final = frozenset(
    {ActionClass.INTERNAL, ActionClass.ESCALATION, ActionClass.STOP, ActionClass.NO_ACTION}
)
CONSERVATIVE_POLICY_VERSION: Final = "phase3-conservative-default-1.0"


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateAction:
    action_type: ActionType
    recovery_probability_basis_points: int
    expected_net_recovery_minor: int
    rank: int
    target: str
    logical_attempt: int = 1
    channel: ContactChannel | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action_type, ActionType):
            raise TypeError("action_type must be an ActionType")
        for name, value, minimum in (
            ("recovery_probability_basis_points", self.recovery_probability_basis_points, 0),
            ("rank", self.rank, 1),
            ("logical_attempt", self.logical_attempt, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        if self.recovery_probability_basis_points > 10_000:
            raise ValueError("recovery probability cannot exceed 10000 basis points")
        if isinstance(self.expected_net_recovery_minor, bool) or not isinstance(
            self.expected_net_recovery_minor, int
        ):
            raise TypeError("expected_net_recovery_minor must be an integer")
        if not self.target:
            raise ValueError("target is required")
        if (
            ACTION_CLASSES[self.action_type] is ActionClass.CUSTOMER_CONTACT
            and self.channel is None
        ):
            raise ValueError("customer-contact actions require a channel")
        if ACTION_CLASSES[self.action_type] is not ActionClass.CUSTOMER_CONTACT and self.channel:
            raise ValueError("only customer-contact actions accept a channel")

    def to_dict(self) -> dict[str, object]:
        return {
            "action_type": self.action_type.value,
            "recovery_probability": self.recovery_probability_basis_points / 10_000,
            "expected_net_recovery_minor": self.expected_net_recovery_minor,
            "rank": self.rank,
            "target": self.target,
            "logical_attempt": self.logical_attempt,
            "channel": self.channel.value if self.channel else None,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class IncidentConstraint:
    incident_id: str
    scope: IncidentScope
    starts_at: datetime
    ends_at: datetime
    channel: ContactChannel | None = None

    def __post_init__(self) -> None:
        if not self.incident_id:
            raise ValueError("incident_id is required")
        starts = _utc("starts_at", self.starts_at)
        ends = _utc("ends_at", self.ends_at)
        object.__setattr__(self, "starts_at", starts)
        object.__setattr__(self, "ends_at", ends)
        if ends <= starts:
            raise ValueError("incident end must follow its start")
        if self.scope is IncidentScope.CONTACT_CHANNEL and self.channel is None:
            raise ValueError("contact-channel incident requires a channel")
        if self.scope is not IncidentScope.CONTACT_CHANNEL and self.channel is not None:
            raise ValueError("channel only applies to contact-channel incidents")

    def applies(self, candidate: CandidateAction, evaluated_at: datetime) -> bool:
        if not self.starts_at <= evaluated_at < self.ends_at:
            return False
        action_class = ACTION_CLASSES[candidate.action_type]
        if action_class in {ActionClass.STOP, ActionClass.NO_ACTION, ActionClass.ESCALATION}:
            return False
        if self.scope is IncidentScope.ALL_AUTOMATION:
            return True
        if self.scope in {
            IncidentScope.PAYMENT_RAIL,
            IncidentScope.GATEWAY,
            IncidentScope.ISSUER,
        }:
            return action_class in {ActionClass.RETRY, ActionClass.MONEY_INTENT}
        return action_class is ActionClass.CUSTOMER_CONTACT and candidate.channel is self.channel


@dataclass(frozen=True, slots=True, kw_only=True)
class MerchantPolicySnapshot:
    version: str
    effective_at: datetime
    allowed_actions: frozenset[ActionType]
    retry_limit: int
    contact_limit: int
    minimum_expected_net_recovery_minor: int
    human_review_amount_minor: int
    minimum_confidence_basis_points: int
    default_defer_seconds: int
    timezone: str
    quiet_hours_start: time
    quiet_hours_end: time
    currency: str = "INR"
    features_version: str = "phase3-v1"

    def __post_init__(self) -> None:
        if not self.version or not self.features_version:
            raise ValueError("policy and feature versions are required")
        object.__setattr__(self, "effective_at", _utc("effective_at", self.effective_at))
        if not self.allowed_actions:
            raise ValueError("allowed_actions cannot be empty")
        if not all(isinstance(action, ActionType) for action in self.allowed_actions):
            raise TypeError("allowed_actions must contain ActionType values")
        for name in (
            "retry_limit",
            "contact_limit",
            "minimum_expected_net_recovery_minor",
            "human_review_amount_minor",
            "minimum_confidence_basis_points",
            "default_defer_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.default_defer_seconds == 0:
            raise ValueError("default_defer_seconds must be positive")
        if self.minimum_confidence_basis_points > 10_000:
            raise ValueError("minimum confidence cannot exceed 10000 basis points")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA zone") from exc
        if not isinstance(self.quiet_hours_start, time) or not isinstance(
            self.quiet_hours_end, time
        ):
            raise TypeError("quiet-hour boundaries must be time values")
        if self.quiet_hours_start.tzinfo or self.quiet_hours_end.tzinfo:
            raise ValueError("quiet-hour boundaries must be local naive times")
        if re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("policy currency must be a three-letter uppercase ISO code")

    def canonical_document(self) -> dict[str, object]:
        return {
            "allowed_actions": sorted(action.value for action in self.allowed_actions),
            "contact_limit": self.contact_limit,
            "currency": self.currency,
            "default_defer_seconds": self.default_defer_seconds,
            "effective_at": _format(self.effective_at),
            "features_version": self.features_version,
            "human_review_amount_minor": self.human_review_amount_minor,
            "minimum_confidence_basis_points": self.minimum_confidence_basis_points,
            "minimum_expected_net_recovery_minor": self.minimum_expected_net_recovery_minor,
            "quiet_hours_end": self.quiet_hours_end.isoformat(timespec="seconds"),
            "quiet_hours_start": self.quiet_hours_start.isoformat(timespec="seconds"),
            "retry_limit": self.retry_limit,
            "timezone": self.timezone,
            "version": self.version,
        }

    @property
    def content_digest(self) -> str:
        canonical = json.dumps(self.canonical_document(), sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyEvaluationInput:
    case_id: str
    amount_minor: int
    currency: str
    confidence_basis_points: int
    retry_count: int
    contact_count: int
    evaluated_at: datetime
    candidates: tuple[CandidateAction, ...]
    evidence_references: tuple[str, ...]
    consent_by_channel: tuple[tuple[ContactChannel, ConsentState], ...] = ()
    opted_out_channels: frozenset[ContactChannel] = frozenset()
    incidents: tuple[IncidentConstraint, ...] = ()
    already_paid: bool = False
    disputed: bool = False
    cancelled: bool = False
    terminal: bool = False
    diagnosis_defer_until: datetime | None = None
    approval: HumanReviewRequest | None = None
    unknown_equivalent_action: bool = False
    customer_contact_in_progress: bool = False
    active_promise_to_pay: bool = False
    promise_due_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.case_id or not self.currency:
            raise ValueError("case_id and currency are required")
        for name in ("amount_minor", "confidence_basis_points", "retry_count", "contact_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.confidence_basis_points > 10_000:
            raise ValueError("confidence cannot exceed 10000 basis points")
        evaluated_at = _utc("evaluated_at", self.evaluated_at)
        object.__setattr__(self, "evaluated_at", evaluated_at)
        if self.diagnosis_defer_until is not None:
            object.__setattr__(
                self,
                "diagnosis_defer_until",
                _utc("diagnosis_defer_until", self.diagnosis_defer_until),
            )
        if not self.candidates or tuple(c.rank for c in self.candidates) != tuple(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("candidate ranks must be ordered and contiguous from one")
        if self.candidates[-1].action_type is not ActionType.NO_ACTION:
            raise ValueError("candidate list must end with NO_ACTION")
        if not self.evidence_references or len(set(self.evidence_references)) != len(
            self.evidence_references
        ):
            raise ValueError("evidence references must be non-empty and unique")
        if not isinstance(self.unknown_equivalent_action, bool):
            raise TypeError("unknown_equivalent_action must be a boolean")
        if not isinstance(self.customer_contact_in_progress, bool):
            raise TypeError("customer_contact_in_progress must be a boolean")
        if not isinstance(self.active_promise_to_pay, bool):
            raise TypeError("active_promise_to_pay must be a boolean")
        if self.promise_due_at is not None:
            object.__setattr__(
                self,
                "promise_due_at",
                _utc("promise_due_at", self.promise_due_at),
            )
        if self.active_promise_to_pay and self.promise_due_at is None:
            raise ValueError("active promise requires promise_due_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionBundle:
    policy: str
    features: str
    application: str
    model: str = "NOT_APPLICABLE"
    prompt: str = "NOT_APPLICABLE"
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, str]:
        return {
            "policy": self.policy,
            "model": self.model,
            "prompt": self.prompt,
            "schema": self.schema,
            "features": self.features,
            "application": self.application,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyDecision:
    selected_action: CandidateAction
    result: PolicyResult
    reason_codes: tuple[str, ...]
    resulting_state: CaseState
    next_evaluation_at: datetime | None
    evaluated_candidates: tuple[CandidateAction, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionReceipt:
    receipt_id: str
    case_id: str
    merchant_id: str
    correlation_id: str
    evidence_references: tuple[str, ...]
    candidate_actions: tuple[CandidateAction, ...]
    selected_action_type: ActionType
    explanation: str
    policy_result: PolicyResult
    policy_reason_codes: tuple[str, ...]
    versions: VersionBundle
    created_at: datetime
    resulting_state: CaseState
    human_review_id: str | None = None
    resulting_action_id: str | None = None
    audit_entry_id: str | None = None
    model_prediction_ids: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("receipt_id", "case_id", "merchant_id", "correlation_id", "explanation"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        if not self.evidence_references or not self.candidate_actions:
            raise ValueError("receipt requires evidence and candidate actions")
        if not self.policy_reason_codes or any(
            REASON_PATTERN.fullmatch(code) is None for code in self.policy_reason_codes
        ):
            raise ValueError("receipt requires machine-readable reason codes")
        if len(set(self.policy_reason_codes)) != len(self.policy_reason_codes):
            raise ValueError("receipt reason codes must be unique")
        if len(set(self.model_prediction_ids)) != len(self.model_prediction_ids) or any(
            not prediction_id for prediction_id in self.model_prediction_ids
        ):
            raise ValueError("model prediction IDs must be non-empty and unique")
        object.__setattr__(self, "created_at", _utc("created_at", self.created_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "correlation_id": self.correlation_id,
            "evidence_references": list(self.evidence_references),
            "candidate_actions": [candidate.to_dict() for candidate in self.candidate_actions],
            "selected_action_type": self.selected_action_type.value,
            "explanation": self.explanation,
            "policy_result": self.policy_result.value,
            "policy_reason_codes": list(self.policy_reason_codes),
            "versions": self.versions.to_dict(),
            "human_review_id": self.human_review_id,
            "resulting_action_id": self.resulting_action_id,
            "resulting_state": self.resulting_state.value,
            "audit_entry_id": self.audit_entry_id,
            "model_prediction_ids": list(self.model_prediction_ids),
            "created_at": _format(self.created_at),
        }


def conservative_default_policy() -> MerchantPolicySnapshot:
    """Return the immutable Test Mode policy seeded by the Phase 3 migration."""

    return MerchantPolicySnapshot(
        version=CONSERVATIVE_POLICY_VERSION,
        effective_at=datetime(2026, 8, 25, tzinfo=UTC),
        allowed_actions=frozenset(ActionType),
        retry_limit=3,
        contact_limit=2,
        minimum_expected_net_recovery_minor=100,
        human_review_amount_minor=50_000,
        minimum_confidence_basis_points=5_000,
        default_defer_seconds=3_600,
        timezone="UTC",
        quiet_hours_start=time(22),
        quiet_hours_end=time(7),
        currency="INR",
    )


def evaluate_policy(
    policy: MerchantPolicySnapshot, evaluation: PolicyEvaluationInput
) -> PolicyDecision:
    if policy.effective_at > evaluation.evaluated_at:
        raise ValueError("policy is not yet effective")
    if policy.currency != evaluation.currency:
        selected = CandidateAction(
            action_type=ActionType.STOP_AUTOMATION,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=1,
            target=evaluation.case_id,
        )
        return PolicyDecision(
            selected_action=selected,
            result=PolicyResult.STOP,
            reason_codes=("POLICY_CURRENCY_MISMATCH",),
            resulting_state=CaseState.STOPPED,
            next_evaluation_at=None,
            evaluated_candidates=(selected,),
        )
    global_reason = _global_stop_reason(evaluation)
    if global_reason:
        selected = CandidateAction(
            action_type=ActionType.STOP_AUTOMATION,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=1,
            target=evaluation.case_id,
        )
        return PolicyDecision(
            selected_action=selected,
            result=PolicyResult.STOP,
            reason_codes=(global_reason,),
            resulting_state=CaseState.STOPPED,
            next_evaluation_at=None,
            evaluated_candidates=(selected,),
        )

    skipped_reasons: list[str] = []
    evaluated: list[CandidateAction] = []
    for candidate in sorted(
        evaluation.candidates, key=lambda item: (item.rank, item.action_type.value)
    ):
        evaluated.append(candidate)
        if candidate.action_type is ActionType.NO_ACTION:
            return PolicyDecision(
                selected_action=candidate,
                result=PolicyResult.SKIP,
                reason_codes=tuple(_unique((*skipped_reasons, "NO_ELIGIBLE_ACTION"))),
                resulting_state=CaseState.DECISION_PENDING,
                next_evaluation_at=None,
                evaluated_candidates=tuple(evaluated),
            )
        result, reason, wake = _evaluate_candidate(policy, evaluation, candidate)
        if result is PolicyResult.SKIP:
            skipped_reasons.append(reason)
            continue
        return PolicyDecision(
            selected_action=candidate,
            result=result,
            reason_codes=tuple(_unique((*skipped_reasons, reason))),
            resulting_state=_state_for(result),
            next_evaluation_at=wake,
            evaluated_candidates=tuple(evaluated),
        )
    raise AssertionError("validated candidate list must contain NO_ACTION")


def _evaluate_candidate(
    policy: MerchantPolicySnapshot,
    evaluation: PolicyEvaluationInput,
    candidate: CandidateAction,
) -> tuple[PolicyResult, str, datetime | None]:
    action_class = ACTION_CLASSES[candidate.action_type]
    if (
        candidate.action_type not in policy.allowed_actions
        and action_class not in ALWAYS_ALLOWED_CLASSES
    ):
        return PolicyResult.SKIP, "ACTION_NOT_ALLOWED", None
    if candidate.action_type is ActionType.ESCALATE_HUMAN:
        # Escalation is a workflow state, never an executable provider action.
        return PolicyResult.REQUIRE_HUMAN, "AGENT_ESCALATION_REQUESTED", None
    if action_class is ActionClass.RETRY and evaluation.retry_count >= policy.retry_limit:
        return PolicyResult.SKIP, "RETRY_LIMIT_REACHED", None
    if action_class is ActionClass.CUSTOMER_CONTACT:
        if evaluation.customer_contact_in_progress:
            return (
                PolicyResult.DEFER,
                "CUSTOMER_CONTACT_ALREADY_IN_PROGRESS",
                evaluation.evaluated_at + timedelta(seconds=policy.default_defer_seconds),
            )
        if evaluation.contact_count >= policy.contact_limit:
            return PolicyResult.SKIP, "CONTACT_LIMIT_REACHED", None
        if candidate.channel in evaluation.opted_out_channels:
            return PolicyResult.SKIP, "CHANNEL_OPTED_OUT", None
        channel = candidate.channel
        if channel is None:
            raise AssertionError("validated customer-contact candidate requires a channel")
        consent = dict(evaluation.consent_by_channel).get(channel, ConsentState.UNKNOWN)
        if consent is not ConsentState.GRANTED:
            return PolicyResult.SKIP, "CHANNEL_CONSENT_NOT_GRANTED", None
    if evaluation.unknown_equivalent_action and action_class in {
        ActionClass.RETRY,
        ActionClass.MONEY_INTENT,
        ActionClass.CUSTOMER_CONTACT,
    }:
        return (
            PolicyResult.DEFER,
            "EQUIVALENT_ACTION_OUTCOME_UNKNOWN",
            evaluation.evaluated_at + timedelta(seconds=policy.default_defer_seconds),
        )
    if (
        evaluation.active_promise_to_pay
        and candidate.action_type is not ActionType.SCHEDULE_PROMISE_REMINDER
        and action_class
        in {
            ActionClass.RETRY,
            ActionClass.MONEY_INTENT,
            ActionClass.CUSTOMER_CONTACT,
        }
    ):
        return PolicyResult.DEFER, "ACTIVE_PROMISE_TO_PAY", evaluation.promise_due_at
    matching_incidents = tuple(
        incident
        for incident in evaluation.incidents
        if incident.applies(candidate, evaluation.evaluated_at)
    )
    if matching_incidents:
        return (
            PolicyResult.DEFER,
            "ACTIVE_INCIDENT",
            min(incident.ends_at for incident in matching_incidents),
        )
    if (
        candidate.action_type is ActionType.DEFER_RETRY
        and evaluation.confidence_basis_points < policy.minimum_confidence_basis_points
        and not _approval_matches(policy, evaluation, candidate)
    ):
        wake = evaluation.diagnosis_defer_until
        if wake is None or wake <= evaluation.evaluated_at:
            wake = evaluation.evaluated_at + timedelta(seconds=policy.default_defer_seconds)
        return PolicyResult.DEFER, "LOW_CONFIDENCE_RETRY_DEFERRED", wake
    if (
        evaluation.diagnosis_defer_until
        and evaluation.evaluated_at < evaluation.diagnosis_defer_until
    ):
        return PolicyResult.DEFER, "DIAGNOSIS_DELAY", evaluation.diagnosis_defer_until
    if action_class is ActionClass.CUSTOMER_CONTACT and _in_quiet_hours(
        policy, evaluation.evaluated_at
    ):
        return (
            PolicyResult.DEFER,
            "QUIET_HOURS",
            _quiet_hours_end(policy, evaluation.evaluated_at),
        )
    if (
        action_class not in EXPECTED_VALUE_EXEMPT_CLASSES
        and candidate.expected_net_recovery_minor < policy.minimum_expected_net_recovery_minor
    ):
        return PolicyResult.SKIP, "EXPECTED_VALUE_BELOW_MINIMUM", None
    needs_review = action_class not in EXPECTED_VALUE_EXEMPT_CLASSES and (
        evaluation.amount_minor >= policy.human_review_amount_minor
        or evaluation.confidence_basis_points < policy.minimum_confidence_basis_points
    )
    if needs_review and not _approval_matches(policy, evaluation, candidate):
        return PolicyResult.REQUIRE_HUMAN, "HUMAN_REVIEW_REQUIRED", None
    return PolicyResult.PROCEED, "POLICY_AUTHORIZED", None


def _approval_matches(
    policy: MerchantPolicySnapshot,
    evaluation: PolicyEvaluationInput,
    candidate: CandidateAction,
) -> bool:
    if evaluation.approval is None:
        return False
    from revenueguard_domain.reviews import ActionFingerprintInput

    fingerprint = ActionFingerprintInput(
        case_id=evaluation.case_id,
        action_type=candidate.action_type.value,
        target=candidate.target,
        amount_minor=evaluation.amount_minor,
        currency=evaluation.currency,
        logical_attempt=candidate.logical_attempt,
        policy_digest=policy.content_digest,
    ).digest()
    return evaluation.approval.is_matching_approval(
        fingerprint=fingerprint,
        policy_digest=policy.content_digest,
        evaluated_at=evaluation.evaluated_at,
    )


def _global_stop_reason(evaluation: PolicyEvaluationInput) -> str | None:
    if evaluation.terminal:
        return "CASE_ALREADY_TERMINAL"
    if evaluation.already_paid:
        return "ALREADY_PAID"
    if evaluation.disputed:
        return "PAYMENT_DISPUTED"
    if evaluation.cancelled:
        return "SUBJECT_CANCELLED"
    return None


def _state_for(result: PolicyResult) -> CaseState:
    return {
        PolicyResult.PROCEED: CaseState.READY,
        PolicyResult.DEFER: CaseState.DEFERRED,
        PolicyResult.SKIP: CaseState.DECISION_PENDING,
        PolicyResult.STOP: CaseState.STOPPED,
        PolicyResult.REQUIRE_HUMAN: CaseState.ESCALATED,
    }[result]


def _in_quiet_hours(policy: MerchantPolicySnapshot, evaluated_at: datetime) -> bool:
    local_time = evaluated_at.astimezone(ZoneInfo(policy.timezone)).timetz().replace(tzinfo=None)
    start, end = policy.quiet_hours_start, policy.quiet_hours_end
    if start == end:
        return False
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end


def _quiet_hours_end(policy: MerchantPolicySnapshot, evaluated_at: datetime) -> datetime:
    zone = ZoneInfo(policy.timezone)
    local = evaluated_at.astimezone(zone)
    candidate = datetime.combine(local.date(), policy.quiet_hours_end, zone)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _unique(values: tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(values))


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _format(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
