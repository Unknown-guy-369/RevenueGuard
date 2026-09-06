"""Forward-only, merchant-scoped audit ledger persistence and verification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from revenueguard_integrations.persistence.models import AuditEntry, AuditLedgerHead

_ZERO_HASH = "0" * 64


class AuditVerificationStatus(StrEnum):
    VALID = "VALID"
    MISSING_GENESIS = "MISSING_GENESIS"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    PREVIOUS_HASH_MISMATCH = "PREVIOUS_HASH_MISMATCH"
    PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"
    ENTRY_HASH_MISMATCH = "ENTRY_HASH_MISMATCH"
    HEAD_MISMATCH = "HEAD_MISMATCH"


@dataclass(frozen=True, slots=True)
class AuditAppend:
    merchant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    correlation_id: str
    actor_type: str
    actor_reference: str
    payload: Mapping[str, object]
    recorded_at: datetime
    causation_id: str | None = None
    policy_version: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    feature_version: str | None = None
    application_version: str | None = None


@dataclass(frozen=True, slots=True)
class AuditVerificationResult:
    merchant_id: str
    status: AuditVerificationStatus
    checked_entries: int
    first_broken_sequence: int | None = None


class AuditLedger:
    """Append audit facts in the caller-owned transaction and verify without mutation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, request: AuditAppend) -> AuditEntry:
        _validate_append(request)
        head = await self._locked_head(request.merchant_id)
        if head.latest_sequence == 0:
            genesis = self._entry_from_request(
                AuditAppend(
                    merchant_id=request.merchant_id,
                    event_type="LEDGER_GENESIS",
                    aggregate_type="MERCHANT",
                    aggregate_id=request.merchant_id,
                    correlation_id=request.correlation_id,
                    actor_type="SYSTEM",
                    actor_reference="audit-ledger",
                    payload={"coverage": "FORWARD_ONLY", "previous_records_backfilled": False},
                    recorded_at=request.recorded_at,
                    application_version=request.application_version,
                ),
                sequence=1,
                previous_entry_hash=_ZERO_HASH,
            )
            self._session.add(genesis)
            head.latest_sequence = genesis.sequence
            head.latest_entry_hash = genesis.entry_hash

        entry = self._entry_from_request(
            request,
            sequence=head.latest_sequence + 1,
            previous_entry_hash=head.latest_entry_hash,
        )
        self._session.add(entry)
        head.latest_sequence = entry.sequence
        head.latest_entry_hash = entry.entry_hash
        await self._session.flush()
        return entry

    async def verify(self, merchant_id: str) -> AuditVerificationResult:
        entries = tuple(
            (
                await self._session.scalars(
                    select(AuditEntry)
                    .where(AuditEntry.merchant_id == merchant_id)
                    .order_by(AuditEntry.sequence)
                )
            ).all()
        )
        head = await self._session.get(AuditLedgerHead, merchant_id)
        if not entries:
            return AuditVerificationResult(merchant_id, AuditVerificationStatus.VALID, 0)
        if head is None:
            return _broken(
                merchant_id, AuditVerificationStatus.HEAD_MISMATCH, 0, entries[0].sequence
            )

        previous_hash = _ZERO_HASH
        for expected_sequence, entry in enumerate(entries, start=1):
            if entry.sequence != expected_sequence:
                return _broken(
                    merchant_id,
                    AuditVerificationStatus.SEQUENCE_GAP,
                    expected_sequence - 1,
                    entry.sequence,
                )
            if expected_sequence == 1 and not _is_genesis(entry):
                return _broken(
                    merchant_id, AuditVerificationStatus.MISSING_GENESIS, 0, entry.sequence
                )
            if entry.previous_entry_hash != previous_hash:
                return _broken(
                    merchant_id,
                    AuditVerificationStatus.PREVIOUS_HASH_MISMATCH,
                    expected_sequence - 1,
                    entry.sequence,
                )
            payload_sha256 = _digest_payload(entry.payload)
            if entry.payload_sha256 != payload_sha256:
                return _broken(
                    merchant_id,
                    AuditVerificationStatus.PAYLOAD_HASH_MISMATCH,
                    expected_sequence - 1,
                    entry.sequence,
                )
            if entry.entry_hash != _entry_digest(entry, payload_sha256):
                return _broken(
                    merchant_id,
                    AuditVerificationStatus.ENTRY_HASH_MISMATCH,
                    expected_sequence - 1,
                    entry.sequence,
                )
            previous_hash = entry.entry_hash

        if head.latest_sequence != len(entries) or head.latest_entry_hash != previous_hash:
            return _broken(
                merchant_id,
                AuditVerificationStatus.HEAD_MISMATCH,
                len(entries),
                head.latest_sequence,
            )
        return AuditVerificationResult(merchant_id, AuditVerificationStatus.VALID, len(entries))

    async def _locked_head(self, merchant_id: str) -> AuditLedgerHead:
        await self._session.execute(
            insert(AuditLedgerHead)
            .values(
                merchant_id=merchant_id,
                latest_sequence=0,
                latest_entry_hash=_ZERO_HASH,
            )
            .on_conflict_do_nothing(index_elements=[AuditLedgerHead.merchant_id])
        )
        return (
            await self._session.scalars(
                select(AuditLedgerHead)
                .where(AuditLedgerHead.merchant_id == merchant_id)
                .with_for_update()
            )
        ).one()

    @staticmethod
    def _entry_from_request(
        request: AuditAppend,
        *,
        sequence: int,
        previous_entry_hash: str,
    ) -> AuditEntry:
        payload = _canonical_payload(request.payload)
        payload_sha256 = _digest_payload(payload)
        entry = AuditEntry(
            merchant_id=request.merchant_id,
            sequence=sequence,
            entry_id=f"audit_{uuid4().hex}",
            event_type=request.event_type,
            aggregate_type=request.aggregate_type,
            aggregate_id=request.aggregate_id,
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
            actor_type=request.actor_type,
            actor_reference=request.actor_reference,
            payload=payload,
            payload_sha256=payload_sha256,
            previous_entry_hash=previous_entry_hash,
            entry_hash="",
            policy_version=request.policy_version,
            model_version=request.model_version,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            feature_version=request.feature_version,
            application_version=request.application_version,
            recorded_at=_utc(request.recorded_at),
        )
        entry.entry_hash = _entry_digest(entry, payload_sha256)
        return entry


def _validate_append(request: AuditAppend) -> None:
    for value in (
        request.merchant_id,
        request.event_type,
        request.aggregate_type,
        request.aggregate_id,
        request.correlation_id,
        request.actor_type,
        request.actor_reference,
    ):
        if not value or len(value) > 128:
            raise ValueError("audit identity fields must be non-empty and at most 128 characters")
    if len(request.event_type) > 64 or len(request.aggregate_type) > 64:
        raise ValueError("audit event and aggregate types must be at most 64 characters")
    _canonical_payload(request.payload)
    _utc(request.recorded_at)


def _canonical_payload(payload: Mapping[str, object]) -> dict[str, Any]:
    value = _canonical_value(payload)
    if not isinstance(value, dict):
        raise ValueError("audit payload must be a mapping")
    return value


def _canonical_value(value: object) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        raise ValueError("audit payloads cannot contain binary floating point")
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("audit payload keys must be strings")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise ValueError(f"audit payload contains unsupported value type: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest_payload(payload: Mapping[str, object]) -> str:
    return sha256(_canonical_bytes(payload)).hexdigest()


def _entry_digest(entry: AuditEntry, payload_sha256: str) -> str:
    return sha256(
        _canonical_bytes(
            {
                "aggregate_id": entry.aggregate_id,
                "aggregate_type": entry.aggregate_type,
                "application_version": entry.application_version,
                "actor_reference": entry.actor_reference,
                "actor_type": entry.actor_type,
                "causation_id": entry.causation_id,
                "correlation_id": entry.correlation_id,
                "entry_id": entry.entry_id,
                "event_type": entry.event_type,
                "feature_version": entry.feature_version,
                "merchant_id": entry.merchant_id,
                "model_version": entry.model_version,
                "payload_sha256": payload_sha256,
                "policy_version": entry.policy_version,
                "previous_entry_hash": entry.previous_entry_hash,
                "prompt_version": entry.prompt_version,
                "recorded_at": _utc(entry.recorded_at).isoformat().replace("+00:00", "Z"),
                "schema_version": entry.schema_version,
                "sequence": entry.sequence,
            }
        )
    ).hexdigest()


def _is_genesis(entry: AuditEntry) -> bool:
    return (
        entry.event_type == "LEDGER_GENESIS"
        and entry.aggregate_type == "MERCHANT"
        and entry.aggregate_id == entry.merchant_id
        and entry.previous_entry_hash == _ZERO_HASH
        and entry.payload == {"coverage": "FORWARD_ONLY", "previous_records_backfilled": False}
    )


def _broken(
    merchant_id: str,
    status: AuditVerificationStatus,
    checked_entries: int,
    sequence: int,
) -> AuditVerificationResult:
    return AuditVerificationResult(
        merchant_id=merchant_id,
        status=status,
        checked_entries=checked_entries,
        first_broken_sequence=sequence,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(UTC)
