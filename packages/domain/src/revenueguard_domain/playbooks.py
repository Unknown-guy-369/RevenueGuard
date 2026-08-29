"""Pure domain contracts for the Phase 6 recovery playbooks."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final

_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")
PLAYBOOK_FEATURE_VERSION: Final = "phase6-playbooks-1.0"


class PromiseIntent(StrEnum):
    """The only intents accepted from bounded customer-response extraction."""

    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    DISPUTE = "DISPUTE"
    ALREADY_PAID = "ALREADY_PAID"
    NEEDS_HELP = "NEEDS_HELP"
    UNKNOWN = "UNKNOWN"


class PromiseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FULFILLED = "FULFILLED"
    BROKEN = "BROKEN"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"


class IncidentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


@dataclass(frozen=True, slots=True, kw_only=True)
class PromiseExtraction:
    """Schema-validated output from a bounded customer-response extractor."""

    intent: PromiseIntent
    confidence_basis_points: int
    extractor_version: str
    promised_date: date | None = None
    amount_minor: int | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, PromiseIntent):
            raise TypeError("intent must be a PromiseIntent")
        if (
            isinstance(self.confidence_basis_points, bool)
            or not isinstance(self.confidence_basis_points, int)
            or not 0 <= self.confidence_basis_points <= 10_000
        ):
            raise ValueError("confidence_basis_points must be between 0 and 10000")
        _identifier("extractor_version", self.extractor_version, 128)
        if self.intent is PromiseIntent.PROMISE_TO_PAY:
            if self.promised_date is None or self.amount_minor is None or self.currency is None:
                raise ValueError("promise-to-pay intent requires date, amount, and currency")
        elif any(
            value is not None for value in (self.promised_date, self.amount_minor, self.currency)
        ):
            raise ValueError("non-promise intents cannot contain promise terms")
        if self.amount_minor is not None:
            if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
                raise TypeError("amount_minor must be an integer")
            if self.amount_minor <= 0:
                raise ValueError("promised amount must be positive")
        if self.currency is not None and _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase ISO code")


@dataclass(frozen=True, slots=True, kw_only=True)
class PromiseToPay:
    """Durable promise terms; raw customer text is deliberately excluded."""

    promise_id: str
    merchant_id: str
    case_id: str
    invoice_id: str
    customer_id: str
    amount_minor: int
    currency: str
    promised_for: date
    reminder_at: datetime
    status: PromiseStatus
    source_response_id: str
    extractor_version: str
    extraction_confidence_basis_points: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("promise_id", self.promise_id),
            ("merchant_id", self.merchant_id),
            ("case_id", self.case_id),
            ("invoice_id", self.invoice_id),
            ("customer_id", self.customer_id),
            ("source_response_id", self.source_response_id),
            ("extractor_version", self.extractor_version),
        ):
            _identifier(name, value, 128)
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be an integer")
        if self.amount_minor <= 0:
            raise ValueError("promise amount must be positive")
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase ISO code")
        if not isinstance(self.status, PromiseStatus):
            raise TypeError("status must be a PromiseStatus")
        if not 0 <= self.extraction_confidence_basis_points <= 10_000:
            raise ValueError("extraction confidence must be between 0 and 10000")
        reminder_at = _utc("reminder_at", self.reminder_at)
        created_at = _utc("created_at", self.created_at)
        updated_at = _utc("updated_at", self.updated_at)
        object.__setattr__(self, "reminder_at", reminder_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        if updated_at < created_at:
            raise ValueError("updated_at cannot precede created_at")


def create_promise(
    *,
    promise_id: str,
    merchant_id: str,
    case_id: str,
    invoice_id: str,
    customer_id: str,
    outstanding_amount_minor: int,
    invoice_currency: str,
    extraction: PromiseExtraction,
    source_response_id: str,
    received_at: datetime,
    reminder_lead: timedelta = timedelta(hours=24),
) -> PromiseToPay:
    """Validate extracted terms and derive a deterministic durable reminder."""

    if extraction.intent is not PromiseIntent.PROMISE_TO_PAY:
        raise ValueError("only PROMISE_TO_PAY extraction can create a promise")
    if extraction.amount_minor is None or extraction.currency is None:
        raise AssertionError("validated promise extraction is missing money terms")
    if extraction.promised_date is None:
        raise AssertionError("validated promise extraction is missing a date")
    if extraction.currency != invoice_currency:
        raise ValueError("promise currency must equal invoice currency")
    if extraction.amount_minor > outstanding_amount_minor:
        raise ValueError("promise amount cannot exceed invoice outstanding amount")
    if reminder_lead < timedelta(0):
        raise ValueError("reminder_lead cannot be negative")
    received = _utc("received_at", received_at)
    promised_at = datetime.combine(extraction.promised_date, datetime.min.time(), UTC)
    if promised_at < received.replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError("promise date cannot be in the past")
    reminder_at = max(received, promised_at - reminder_lead)
    return PromiseToPay(
        promise_id=promise_id,
        merchant_id=merchant_id,
        case_id=case_id,
        invoice_id=invoice_id,
        customer_id=customer_id,
        amount_minor=extraction.amount_minor,
        currency=extraction.currency,
        promised_for=extraction.promised_date,
        reminder_at=reminder_at,
        status=PromiseStatus.ACTIVE,
        source_response_id=source_response_id,
        extractor_version=extraction.extractor_version,
        extraction_confidence_basis_points=extraction.confidence_basis_points,
        created_at=received,
        updated_at=received,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentOutcomeObservation:
    observation_id: str
    merchant_id: str
    payment_id: str
    succeeded: bool
    payment_method: str
    issuer_family: str
    error_family: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("observation_id", self.observation_id),
            ("merchant_id", self.merchant_id),
            ("payment_id", self.payment_id),
            ("payment_method", self.payment_method),
            ("issuer_family", self.issuer_family),
            ("error_family", self.error_family),
        ):
            _identifier(name, value, 128)
        if not isinstance(self.succeeded, bool):
            raise TypeError("succeeded must be a boolean")
        object.__setattr__(self, "occurred_at", _utc("occurred_at", self.occurred_at))


@dataclass(frozen=True, slots=True, kw_only=True)
class DegradationPolicy:
    baseline_window: timedelta = timedelta(hours=24)
    current_window: timedelta = timedelta(minutes=15)
    minimum_baseline_count: int = 20
    minimum_current_count: int = 10
    minimum_failure_rate_basis_points: int = 2_500
    minimum_rate_increase_basis_points: int = 1_500
    minimum_rate_ratio_basis_points: int = 20_000
    incident_ttl: timedelta = timedelta(minutes=30)
    clear_consecutive_windows: int = 2
    version: str = PLAYBOOK_FEATURE_VERSION

    def __post_init__(self) -> None:
        if self.baseline_window <= self.current_window or self.current_window <= timedelta(0):
            raise ValueError("baseline window must be longer than the positive current window")
        if self.incident_ttl <= timedelta(0):
            raise ValueError("incident_ttl must be positive")
        for name in (
            "minimum_baseline_count",
            "minimum_current_count",
            "minimum_failure_rate_basis_points",
            "minimum_rate_increase_basis_points",
            "minimum_rate_ratio_basis_points",
            "clear_consecutive_windows",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.minimum_baseline_count == 0 or self.minimum_current_count == 0:
            raise ValueError("sample-count thresholds must be positive")
        if self.clear_consecutive_windows == 0:
            raise ValueError("clear_consecutive_windows must be positive")
        for name in (
            "minimum_failure_rate_basis_points",
            "minimum_rate_increase_basis_points",
        ):
            if getattr(self, name) > 10_000:
                raise ValueError(f"{name} cannot exceed 10000")
        _identifier("version", self.version, 128)


@dataclass(frozen=True, slots=True, kw_only=True)
class DegradationAssessment:
    merchant_id: str
    payment_method: str
    issuer_family: str
    error_family: str
    baseline_total: int
    baseline_failures: int
    current_total: int
    current_failures: int
    baseline_failure_rate_basis_points: int
    current_failure_rate_basis_points: int
    failure_rate_increase_basis_points: int
    failure_rate_ratio_basis_points: int
    degraded: bool
    evaluated_at: datetime
    policy_version: str


def assess_payment_degradation(
    observations: tuple[PaymentOutcomeObservation, ...],
    *,
    evaluated_at: datetime,
    policy: DegradationPolicy,
) -> tuple[DegradationAssessment, ...]:
    """Compare a recent window with the immediately preceding transparent baseline."""

    evaluated = _utc("evaluated_at", evaluated_at)
    baseline_start = evaluated - policy.baseline_window
    current_start = evaluated - policy.current_window
    grouped: dict[
        tuple[str, str, str, str],
        dict[str, list[PaymentOutcomeObservation]],
    ] = defaultdict(lambda: {"baseline": [], "current": []})
    for observation in observations:
        if observation.occurred_at > evaluated or observation.occurred_at < baseline_start:
            continue
        key = (
            observation.merchant_id,
            observation.payment_method,
            observation.issuer_family,
            observation.error_family,
        )
        bucket = "current" if observation.occurred_at >= current_start else "baseline"
        grouped[key][bucket].append(observation)

    assessments: list[DegradationAssessment] = []
    for key, windows in sorted(grouped.items()):
        baseline_total = len(windows["baseline"])
        current_total = len(windows["current"])
        baseline_failures = sum(not item.succeeded for item in windows["baseline"])
        current_failures = sum(not item.succeeded for item in windows["current"])
        baseline_rate = _rate_basis_points(baseline_failures, baseline_total)
        current_rate = _rate_basis_points(current_failures, current_total)
        increase = current_rate - baseline_rate
        ratio = (
            current_rate * 10_000 // baseline_rate
            if baseline_rate > 0
            else (100_000 if current_rate > 0 else 10_000)
        )
        degraded = (
            baseline_total >= policy.minimum_baseline_count
            and current_total >= policy.minimum_current_count
            and current_rate >= policy.minimum_failure_rate_basis_points
            and increase >= policy.minimum_rate_increase_basis_points
            and ratio >= policy.minimum_rate_ratio_basis_points
        )
        assessments.append(
            DegradationAssessment(
                merchant_id=key[0],
                payment_method=key[1],
                issuer_family=key[2],
                error_family=key[3],
                baseline_total=baseline_total,
                baseline_failures=baseline_failures,
                current_total=current_total,
                current_failures=current_failures,
                baseline_failure_rate_basis_points=baseline_rate,
                current_failure_rate_basis_points=current_rate,
                failure_rate_increase_basis_points=increase,
                failure_rate_ratio_basis_points=ratio,
                degraded=degraded,
                evaluated_at=evaluated,
                policy_version=policy.version,
            )
        )
    return tuple(assessments)


def _rate_basis_points(failures: int, total: int) -> int:
    return failures * 10_000 // total if total else 0


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
