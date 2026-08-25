"""Human-review lifecycle and action-bound approval evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256


class ReviewStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ReviewDecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionFingerprintInput:
    case_id: str
    action_type: str
    target: str
    amount_minor: int
    currency: str
    logical_attempt: int
    policy_digest: str

    def __post_init__(self) -> None:
        for name in ("case_id", "action_type", "target", "currency", "policy_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} is required")
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be an integer")
        if self.amount_minor < 0:
            raise ValueError("amount_minor cannot be negative")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase code")
        if isinstance(self.logical_attempt, bool) or not isinstance(self.logical_attempt, int):
            raise TypeError("logical_attempt must be an integer")
        if self.logical_attempt < 0:
            raise ValueError("logical_attempt cannot be negative")

    def digest(self) -> str:
        document = {
            "action_type": self.action_type,
            "amount_minor": self.amount_minor,
            "case_id": self.case_id,
            "currency": self.currency,
            "logical_attempt": self.logical_attempt,
            "policy_digest": self.policy_digest,
            "target": self.target,
        }
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanReviewRequest:
    review_id: str
    merchant_id: str
    case_id: str
    action_fingerprint: str
    proposed_action_type: str
    evidence_references: tuple[str, ...]
    policy_version: str
    policy_digest: str
    reason_code: str
    risk_detail: str
    requested_at: datetime
    expires_at: datetime
    status: ReviewStatus = ReviewStatus.REQUESTED
    reviewer_id: str | None = None
    rationale: str | None = None
    decided_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "review_id",
            "merchant_id",
            "case_id",
            "action_fingerprint",
            "proposed_action_type",
            "policy_version",
            "policy_digest",
            "reason_code",
            "risk_detail",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} is required")
        if len(self.action_fingerprint) != 64 or len(self.policy_digest) != 64:
            raise ValueError("fingerprint and policy digest must be SHA-256 hex digests")
        if not self.evidence_references or len(set(self.evidence_references)) != len(
            self.evidence_references
        ):
            raise ValueError("evidence references must be non-empty and unique")
        requested_at = _utc("requested_at", self.requested_at)
        expires_at = _utc("expires_at", self.expires_at)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "expires_at", expires_at)
        if expires_at <= requested_at:
            raise ValueError("review expiry must follow request time")
        decided = self.decided_at
        if decided is not None:
            decided = _utc("decided_at", decided)
            object.__setattr__(self, "decided_at", decided)
        if self.status is ReviewStatus.REQUESTED:
            if self.reviewer_id or self.rationale or decided:
                raise ValueError("requested review cannot contain decision metadata")
        else:
            if not self.reviewer_id or not self.rationale or decided is None:
                raise ValueError("decided review requires reviewer, rationale, and time")
            if decided < requested_at:
                raise ValueError("review decision cannot precede its request")
            if self.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
                if decided >= expires_at:
                    raise ValueError("approval or rejection must precede review expiry")
            elif self.status is ReviewStatus.EXPIRED and decided < expires_at:
                raise ValueError("expired review decision cannot precede expiry")

    def is_matching_approval(
        self, *, fingerprint: str, policy_digest: str, evaluated_at: datetime
    ) -> bool:
        now = _utc("evaluated_at", evaluated_at)
        return (
            self.status is ReviewStatus.APPROVED
            and now < self.expires_at
            and self.action_fingerprint == fingerprint
            and self.policy_digest == policy_digest
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanReviewDecision:
    review_id: str
    decision: ReviewDecisionType
    reviewer_id: str
    rationale: str
    decided_at: datetime

    def __post_init__(self) -> None:
        for name in ("review_id", "reviewer_id", "rationale"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "decided_at", _utc("decided_at", self.decided_at))


def decide_review(request: HumanReviewRequest, decision: HumanReviewDecision) -> HumanReviewRequest:
    if request.status is not ReviewStatus.REQUESTED:
        raise ValueError("only a requested review can be decided")
    if request.review_id != decision.review_id:
        raise ValueError("review decision targets a different request")
    if decision.decided_at >= request.expires_at:
        raise ValueError("expired review cannot be approved or rejected")
    if decision.decided_at < request.requested_at:
        raise ValueError("review decision cannot precede its request")
    status = (
        ReviewStatus.APPROVED
        if decision.decision is ReviewDecisionType.APPROVE
        else ReviewStatus.REJECTED
    )
    return replace(
        request,
        status=status,
        reviewer_id=decision.reviewer_id,
        rationale=decision.rationale,
        decided_at=decision.decided_at,
    )


def expire_review(
    request: HumanReviewRequest, *, expired_at: datetime, actor: str = "SYSTEM"
) -> HumanReviewRequest:
    now = _utc("expired_at", expired_at)
    if request.status is not ReviewStatus.REQUESTED:
        raise ValueError("only a requested review can expire")
    if now < request.expires_at:
        raise ValueError("review has not reached its expiry")
    if not actor:
        raise ValueError("actor is required")
    return replace(
        request,
        status=ReviewStatus.EXPIRED,
        reviewer_id=actor,
        rationale="REVIEW_EXPIRED",
        decided_at=now,
    )


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
