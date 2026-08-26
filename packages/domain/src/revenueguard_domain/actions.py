"""Idempotent recovery-action and verified-outcome domain contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Final

from revenueguard_domain.cases import SubjectType
from revenueguard_domain.policy import ActionType

SCHEMA_VERSION: Final = "1.0"
_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$")


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class EvidenceSource(StrEnum):
    SIGNED_WEBHOOK = "SIGNED_WEBHOOK"
    PROVIDER_LOOKUP = "PROVIDER_LOOKUP"
    PROVIDER_RESPONSE = "PROVIDER_RESPONSE"
    SIMULATOR = "SIMULATOR"
    NONE = "NONE"


def action_idempotency_key(
    *,
    merchant_id: str,
    case_id: str,
    action_type: ActionType,
    target_type: SubjectType,
    target_id: str,
    logical_attempt: int,
) -> str:
    """Derive a stable key from logical business identity, never worker identity."""

    for name, value in (
        ("merchant_id", merchant_id),
        ("case_id", case_id),
        ("target_id", target_id),
    ):
        _identifier(name, value, 128)
    if isinstance(logical_attempt, bool) or not isinstance(logical_attempt, int):
        raise TypeError("logical_attempt must be an integer")
    if logical_attempt < 1:
        raise ValueError("logical_attempt must be at least one")
    document = {
        "action_type": action_type.value,
        "case_id": case_id,
        "logical_attempt": logical_attempt,
        "merchant_id": merchant_id,
        "target_id": target_id,
        "target_type": target_type.value,
        "version": 1,
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return f"rg:v1:{sha256(canonical.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryAction:
    action_id: str
    case_id: str
    merchant_id: str
    decision_receipt_id: str
    action_type: ActionType
    target_type: SubjectType
    target_id: str
    logical_attempt: int
    idempotency_key: str
    status: ActionStatus
    parameters: Mapping[str, object]
    authorized_at: datetime
    execute_after: datetime
    created_at: datetime
    updated_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported RecoveryAction schema")
        for name, value in (
            ("action_id", self.action_id),
            ("case_id", self.case_id),
            ("merchant_id", self.merchant_id),
            ("decision_receipt_id", self.decision_receipt_id),
            ("target_id", self.target_id),
        ):
            _identifier(name, value, 128)
        if not isinstance(self.action_type, ActionType):
            raise TypeError("action_type must be an ActionType")
        if not isinstance(self.target_type, SubjectType):
            raise TypeError("target_type must be a SubjectType")
        if not isinstance(self.status, ActionStatus):
            raise TypeError("status must be an ActionStatus")
        if isinstance(self.logical_attempt, bool) or not isinstance(self.logical_attempt, int):
            raise TypeError("logical_attempt must be an integer")
        if self.logical_attempt < 1:
            raise ValueError("logical_attempt must be at least one")
        expected_key = action_idempotency_key(
            merchant_id=self.merchant_id,
            case_id=self.case_id,
            action_type=self.action_type,
            target_type=self.target_type,
            target_id=self.target_id,
            logical_attempt=self.logical_attempt,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("idempotency_key does not match logical action identity")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("parameters must be a mapping")
        amount = self.parameters.get("amount_minor")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValueError("parameters.amount_minor must be a non-negative integer")
        currency = self.parameters.get("currency")
        if not isinstance(currency, str) or _CURRENCY_PATTERN.fullmatch(currency) is None:
            raise ValueError("parameters.currency must be a three-letter uppercase ISO code")
        if self.parameters.get("provider_mode") != "TEST":
            raise ValueError("only TEST provider mode is supported")
        authorized = _utc("authorized_at", self.authorized_at)
        execute_after = _utc("execute_after", self.execute_after)
        created = _utc("created_at", self.created_at)
        object.__setattr__(self, "authorized_at", authorized)
        object.__setattr__(self, "execute_after", execute_after)
        object.__setattr__(self, "created_at", created)
        if execute_after < authorized or created < authorized:
            raise ValueError("action chronology is invalid")
        if self.updated_at is not None:
            updated = _utc("updated_at", self.updated_at)
            object.__setattr__(self, "updated_at", updated)
            if updated < created:
                raise ValueError("updated_at cannot precede created_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "decision_receipt_id": self.decision_receipt_id,
            "action_type": self.action_type.value,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "logical_attempt": self.logical_attempt,
            "idempotency_key": self.idempotency_key,
            "status": self.status.value,
            "parameters": dict(self.parameters),
            "authorized_at": _format(self.authorized_at),
            "execute_after": _format(self.execute_after),
            "created_at": _format(self.created_at),
            "updated_at": _format(self.updated_at),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedOutcome:
    outcome_id: str
    action_id: str
    case_id: str
    merchant_id: str
    outcome_status: ActionStatus
    is_authoritative: bool
    evidence_source: EvidenceSource
    recovered_amount_minor: int
    currency: str
    observed_at: datetime
    created_at: datetime
    evidence_reference: str | None = None
    provider_object_id: str | None = None
    reason_code: str | None = None
    verified_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported VerifiedOutcome schema")
        for name, value in (
            ("outcome_id", self.outcome_id),
            ("action_id", self.action_id),
            ("case_id", self.case_id),
            ("merchant_id", self.merchant_id),
        ):
            _identifier(name, value, 128)
        if not isinstance(self.outcome_status, ActionStatus):
            raise TypeError("outcome_status must be an ActionStatus")
        if not isinstance(self.evidence_source, EvidenceSource):
            raise TypeError("evidence_source must be an EvidenceSource")
        if not isinstance(self.is_authoritative, bool):
            raise TypeError("is_authoritative must be a boolean")
        if isinstance(self.recovered_amount_minor, bool) or not isinstance(
            self.recovered_amount_minor, int
        ):
            raise TypeError("recovered_amount_minor must be an integer")
        if self.recovered_amount_minor < 0:
            raise ValueError("recovered_amount_minor cannot be negative")
        if _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase ISO code")
        if self.outcome_status is ActionStatus.UNKNOWN:
            if self.is_authoritative or self.recovered_amount_minor or self.verified_at:
                raise ValueError("UNKNOWN outcomes cannot be authoritative or recover money")
        if self.recovered_amount_minor:
            if self.outcome_status is not ActionStatus.SUCCEEDED or not self.is_authoritative:
                raise ValueError("recovered money requires authoritative success")
            if self.evidence_reference is None or self.verified_at is None:
                raise ValueError("recovered money requires evidence and verification time")
        if self.is_authoritative and self.verified_at is None:
            raise ValueError("authoritative outcomes require verified_at")
        observed = _utc("observed_at", self.observed_at)
        created = _utc("created_at", self.created_at)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "created_at", created)
        if self.verified_at is not None:
            verified = _utc("verified_at", self.verified_at)
            object.__setattr__(self, "verified_at", verified)
            if verified < observed:
                raise ValueError("verified_at cannot precede observed_at")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "outcome_id": self.outcome_id,
            "action_id": self.action_id,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "outcome_status": self.outcome_status.value,
            "is_authoritative": self.is_authoritative,
            "evidence_source": self.evidence_source.value,
            "evidence_reference": self.evidence_reference,
            "provider_object_id": self.provider_object_id,
            "recovered_amount_minor": self.recovered_amount_minor,
            "currency": self.currency,
            "reason_code": self.reason_code,
            "observed_at": _format(self.observed_at),
            "verified_at": _format(self.verified_at),
            "created_at": _format(self.created_at),
        }


def _identifier(name: str, value: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters")


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _format(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None
