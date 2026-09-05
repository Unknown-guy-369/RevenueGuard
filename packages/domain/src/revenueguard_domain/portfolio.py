"""Deterministic, policy-bounded portfolio recovery scoring."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from revenueguard_domain.policy import (
    ACTION_CLASSES,
    ALWAYS_ALLOWED_CLASSES,
    EXPECTED_VALUE_EXEMPT_CLASSES,
    ActionType,
    CandidateAction,
)

SCORING_FEATURE_NAMES: Final = (
    "amount_bucket",
    "retry_count",
    "aggregate_contact_count",
    "diagnosis_confidence_decile",
    "active_systemic_incident",
    "hour_bucket",
    "day_of_month_bucket",
)


class ArtifactClassification(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    PRODUCTION = "PRODUCTION"


class CustomerInterventionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True, kw_only=True)
class CustomerIntervention:
    intervention_id: str
    merchant_id: str
    customer_id: str
    owner_case_id: str
    action_id: str
    coordinated_case_ids: tuple[str, ...]
    status: CustomerInterventionStatus
    cooldown_until: datetime
    model_version: str
    policy_version: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    close_reason: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "intervention_id",
            "merchant_id",
            "customer_id",
            "owner_case_id",
            "action_id",
            "model_version",
            "policy_version",
        ):
            _identifier(name, getattr(self, name))
        if not self.coordinated_case_ids or len(set(self.coordinated_case_ids)) != len(
            self.coordinated_case_ids
        ):
            raise ValueError("coordinated_case_ids must be non-empty and unique")
        if self.owner_case_id not in self.coordinated_case_ids:
            raise ValueError("owner case must be included in coordinated cases")
        if not isinstance(self.status, CustomerInterventionStatus):
            raise TypeError("status must be a CustomerInterventionStatus")
        for name in ("cooldown_until", "created_at", "updated_at"):
            value = _utc(name, getattr(self, name))
            object.__setattr__(self, name, value)
        if self.cooldown_until <= self.created_at:
            raise ValueError("cooldown_until must follow created_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.status is CustomerInterventionStatus.ACTIVE:
            if self.closed_at is not None or self.close_reason is not None:
                raise ValueError("active intervention cannot have close metadata")
        else:
            if self.closed_at is None or not self.close_reason:
                raise ValueError("closed intervention requires close metadata")
            closed_at = _utc("closed_at", self.closed_at)
            object.__setattr__(self, "closed_at", closed_at)
            if closed_at < self.created_at:
                raise ValueError("closed_at cannot precede created_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class LogisticScoringArtifact:
    model_version: str
    feature_version: str
    classification: ArtifactClassification
    intercept_millilogits: int
    feature_weights_millilogits: Mapping[str, int]
    action_bias_millilogits: Mapping[ActionType, int]
    failure_category_bias_millilogits: Mapping[str, int]

    def __post_init__(self) -> None:
        _identifier("model_version", self.model_version)
        _identifier("feature_version", self.feature_version)
        if not isinstance(self.classification, ArtifactClassification):
            raise TypeError("classification must be an ArtifactClassification")
        if isinstance(self.intercept_millilogits, bool) or not isinstance(
            self.intercept_millilogits, int
        ):
            raise TypeError("intercept_millilogits must be an integer")
        weights = dict(self.feature_weights_millilogits)
        if set(weights) != set(SCORING_FEATURE_NAMES):
            raise ValueError("feature weights must exactly match the Phase 7 feature schema")
        _integer_values("feature_weights_millilogits", weights.values())
        action_biases = dict(self.action_bias_millilogits)
        if not all(isinstance(action, ActionType) for action in action_biases):
            raise TypeError("action bias keys must be ActionType values")
        _integer_values("action_bias_millilogits", action_biases.values())
        failure_biases = dict(self.failure_category_bias_millilogits)
        if not all(isinstance(name, str) and name for name in failure_biases):
            raise ValueError("failure-category bias keys must be non-empty strings")
        _integer_values("failure_category_bias_millilogits", failure_biases.values())
        object.__setattr__(self, "feature_weights_millilogits", MappingProxyType(weights))
        object.__setattr__(self, "action_bias_millilogits", MappingProxyType(action_biases))
        object.__setattr__(
            self,
            "failure_category_bias_millilogits",
            MappingProxyType(failure_biases),
        )

    @classmethod
    def zero_weighted(
        cls,
        *,
        model_version: str,
        feature_version: str,
        classification: ArtifactClassification,
    ) -> LogisticScoringArtifact:
        return cls(
            model_version=model_version,
            feature_version=feature_version,
            classification=classification,
            intercept_millilogits=0,
            feature_weights_millilogits={name: 0 for name in SCORING_FEATURE_NAMES},
            action_bias_millilogits={},
            failure_category_bias_millilogits={},
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionEconomics:
    version: str
    action_cost_minor: Mapping[ActionType, int]
    risk_penalty_minor: Mapping[ActionType, int]
    customer_friction_penalty_minor: Mapping[ActionType, int]

    def __post_init__(self) -> None:
        _identifier("version", self.version)
        for name in (
            "action_cost_minor",
            "risk_penalty_minor",
            "customer_friction_penalty_minor",
        ):
            values = dict(getattr(self, name))
            if not all(isinstance(action, ActionType) for action in values):
                raise TypeError(f"{name} keys must be ActionType values")
            for value in values.values():
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{name} values must be non-negative integers")
            object.__setattr__(self, name, MappingProxyType(values))


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryScoringContext:
    amount_minor: int
    retry_count: int
    aggregate_contact_count: int
    diagnosis_confidence_basis_points: int
    failure_category: str
    active_systemic_incident: bool
    evaluated_at: datetime

    def __post_init__(self) -> None:
        for name in ("amount_minor", "retry_count", "aggregate_contact_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        confidence = self.diagnosis_confidence_basis_points
        if isinstance(confidence, bool) or not isinstance(confidence, int):
            raise TypeError("diagnosis_confidence_basis_points must be an integer")
        if not 0 <= confidence <= 10_000:
            raise ValueError("diagnosis confidence must be between 0 and 10000")
        _identifier("failure_category", self.failure_category)
        if not isinstance(self.active_systemic_incident, bool):
            raise TypeError("active_systemic_incident must be a boolean")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(UTC))


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryScoringResult:
    candidates: tuple[CandidateAction, ...]
    model_version: str
    feature_version: str
    economics_version: str
    artifact_classification: ArtifactClassification
    fallback_reason: str | None = None


def synthetic_default_scoring_artifact() -> LogisticScoringArtifact:
    """Return the explicitly synthetic demo scorer; it is not merchant calibration evidence."""

    return LogisticScoringArtifact(
        model_version="phase7-synthetic-logit-1.0",
        feature_version="phase7-portfolio-features-1.0",
        classification=ArtifactClassification.SYNTHETIC,
        intercept_millilogits=-200,
        feature_weights_millilogits={
            "amount_bucket": 8,
            "retry_count": -300,
            "aggregate_contact_count": -450,
            "diagnosis_confidence_decile": 90,
            "active_systemic_incident": -2_000,
            "hour_bucket": 20,
            "day_of_month_bucket": 15,
        },
        action_bias_millilogits={
            ActionType.DEFER_RETRY: 800,
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 700,
            ActionType.CREATE_PAYMENT_LINK: 500,
            ActionType.SEND_REMINDER: 400,
            ActionType.SCHEDULE_PROMISE_REMINDER: 400,
            ActionType.PAUSE_RETRIES: 100,
            ActionType.RESUME_DEFERRED_CASE: 100,
            ActionType.ESCALATE_HUMAN: -1_000,
            ActionType.STOP_AUTOMATION: -2_000,
        },
        failure_category_bias_millilogits={
            "INSUFFICIENT_FUNDS": 100,
            "EXPIRED_PAYMENT_METHOD": 250,
            "AUTHENTICATION_FAILURE": 100,
            "CUSTOMER_ACTION_REQUIRED": 150,
            "ISSUER_UNAVAILABLE": -700,
            "GATEWAY_UNAVAILABLE": -900,
            "UNKNOWN": -1_200,
        },
    )


def default_action_economics() -> ActionEconomics:
    return ActionEconomics(
        version="phase7-action-economics-1.0",
        action_cost_minor={
            ActionType.DEFER_RETRY: 25,
            ActionType.CREATE_PAYMENT_LINK: 75,
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 100,
            ActionType.SEND_REMINDER: 100,
            ActionType.SCHEDULE_PROMISE_REMINDER: 100,
        },
        risk_penalty_minor={
            ActionType.DEFER_RETRY: 50,
            ActionType.CREATE_PAYMENT_LINK: 100,
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 100,
            ActionType.SEND_REMINDER: 75,
            ActionType.SCHEDULE_PROMISE_REMINDER: 75,
        },
        customer_friction_penalty_minor={
            ActionType.DEFER_RETRY: 25,
            ActionType.CREATE_PAYMENT_LINK: 75,
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 250,
            ActionType.SEND_REMINDER: 200,
            ActionType.SCHEDULE_PROMISE_REMINDER: 150,
        },
    )


def rank_candidates_by_expected_net_recovery(
    candidates: tuple[CandidateAction, ...],
    *,
    context: RecoveryScoringContext,
    artifact: LogisticScoringArtifact,
    economics: ActionEconomics,
    allowed_actions: frozenset[ActionType],
) -> RecoveryScoringResult:
    """Score policy-compatible candidates while retaining a final no-action fallback."""

    if not candidates or candidates[-1].action_type is not ActionType.NO_ACTION:
        raise ValueError("candidate list must end with NO_ACTION")
    scored: list[CandidateAction] = []
    eligible_original_order: list[CandidateAction] = []
    no_action: CandidateAction | None = None
    feature_values = _feature_values(context)
    for candidate in candidates:
        if candidate.action_type is ActionType.NO_ACTION:
            no_action = replace(
                candidate,
                recovery_probability_basis_points=0,
                expected_net_recovery_minor=0,
                action_cost_minor=0,
                risk_penalty_minor=0,
                customer_friction_penalty_minor=0,
            )
            continue
        action_class = ACTION_CLASSES[candidate.action_type]
        if (
            candidate.action_type not in allowed_actions
            and action_class not in ALWAYS_ALLOWED_CLASSES
        ):
            continue
        eligible_original_order.append(candidate)
        if action_class in EXPECTED_VALUE_EXEMPT_CLASSES:
            continue
        probability = _probability_basis_points(
            artifact=artifact,
            action_type=candidate.action_type,
            failure_category=context.failure_category,
            feature_values=feature_values,
        )
        action_cost = economics.action_cost_minor.get(candidate.action_type, 0)
        risk_penalty = economics.risk_penalty_minor.get(candidate.action_type, 0)
        friction_penalty = economics.customer_friction_penalty_minor.get(candidate.action_type, 0)
        expected_gross = probability * context.amount_minor // 10_000
        scored.append(
            replace(
                candidate,
                recovery_probability_basis_points=probability,
                expected_net_recovery_minor=(
                    expected_gross - action_cost - risk_penalty - friction_penalty
                ),
                action_cost_minor=action_cost,
                risk_penalty_minor=risk_penalty,
                customer_friction_penalty_minor=friction_penalty,
            )
        )
    if no_action is None:
        raise AssertionError("validated candidates must contain NO_ACTION")
    scored.sort(
        key=lambda item: (
            -item.expected_net_recovery_minor,
            -item.recovery_probability_basis_points,
            item.action_type.value,
        )
    )
    scored_iterator = iter(scored)
    ordered = tuple(
        candidate
        if ACTION_CLASSES[candidate.action_type] in EXPECTED_VALUE_EXEMPT_CLASSES
        else next(scored_iterator)
        for candidate in eligible_original_order
    )
    ranked = tuple(
        replace(candidate, rank=index)
        for index, candidate in enumerate((*ordered, no_action), start=1)
    )
    return RecoveryScoringResult(
        candidates=ranked,
        model_version=artifact.model_version,
        feature_version=artifact.feature_version,
        economics_version=economics.version,
        artifact_classification=artifact.classification,
    )


def _feature_values(context: RecoveryScoringContext) -> Mapping[str, int]:
    return {
        "amount_bucket": min(context.amount_minor // 10_000, 100),
        "retry_count": min(context.retry_count, 10),
        "aggregate_contact_count": min(context.aggregate_contact_count, 10),
        "diagnosis_confidence_decile": context.diagnosis_confidence_basis_points // 1_000,
        "active_systemic_incident": int(context.active_systemic_incident),
        "hour_bucket": context.evaluated_at.hour // 6,
        "day_of_month_bucket": (context.evaluated_at.day - 1) // 7,
    }


def _probability_basis_points(
    *,
    artifact: LogisticScoringArtifact,
    action_type: ActionType,
    failure_category: str,
    feature_values: Mapping[str, int],
) -> int:
    millilogits = artifact.intercept_millilogits
    millilogits += artifact.action_bias_millilogits.get(action_type, 0)
    millilogits += artifact.failure_category_bias_millilogits.get(failure_category, 0)
    millilogits += sum(
        artifact.feature_weights_millilogits[name] * feature_values[name]
        for name in SCORING_FEATURE_NAMES
    )
    millilogits = max(-20_000, min(20_000, millilogits))
    with localcontext() as decimal_context:
        decimal_context.prec = 40
        logit = Decimal(millilogits) / Decimal(1_000)
        probability = Decimal(1) / (Decimal(1) + (-logit).exp())
        return int((probability * Decimal(10_000)).to_integral_value(rounding=ROUND_HALF_UP))


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{name} must contain 1 to 128 characters")


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _integer_values(name: str, values: Iterable[object]) -> None:
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} values must be integers")
