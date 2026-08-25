"""Shared provider-status ordering for persistence and evidence decisions."""

from __future__ import annotations

from typing import Final

PROVIDER_STATUS_PRECEDENCE: Final = {
    "AUTHORIZED": 50,
    "CAPTURED": 100,
    "PAID": 100,
    "CHARGED": 100,
    "COMPLETED": 100,
    "DISPUTED": 110,
    "CANCELLED": 90,
    "HALTED": 90,
    "FAILED": 20,
    "PENDING": 10,
    "CREATED": 5,
}


def compare_provider_status(left: str | None, right: str | None) -> int:
    """Compare normalized statuses without treating lexical order as chronology."""

    left_rank = PROVIDER_STATUS_PRECEDENCE.get((left or "").upper(), 0)
    right_rank = PROVIDER_STATUS_PRECEDENCE.get((right or "").upper(), 0)
    return (left_rank > right_rank) - (left_rank < right_rank)
