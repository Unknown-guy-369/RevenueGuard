from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from revenueguard_integrations.persistence import (
    EvidenceDisposition,
    EvidenceLinkResult,
    NormalizedEvent,
    RecoveryCaseEvent,
    RecoveryRepository,
)
from revenueguard_integrations.recovery import RecoveryApplicationService

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


class AuditOnlyRepository:
    def __init__(self, event: NormalizedEvent | None) -> None:
        self.event = event
        self.locked_merchant: str | None = None
        self.links: list[RecoveryCaseEvent] = []

    async def lock_merchant(self, *, merchant_id: str) -> None:
        self.locked_merchant = merchant_id

    async def get_normalized_event(
        self, *, merchant_id: str, normalized_event_id: str
    ) -> NormalizedEvent | None:
        if self.event is None:
            return None
        if self.event.merchant_id != merchant_id or self.event.id != normalized_event_id:
            return None
        return self.event

    async def find_active_case(self, **_: Any) -> None:
        return None

    async def find_episode_case(self, **_: Any) -> None:
        return None

    async def link_evidence(
        self,
        *,
        merchant_id: str,
        normalized_event_id: str,
        recovery_case_id: str | None,
        disposition: EvidenceDisposition,
        reason_code: str,
    ) -> EvidenceLinkResult:
        row = RecoveryCaseEvent(
            merchant_id=merchant_id,
            normalized_event_id=normalized_event_id,
            recovery_case_id=recovery_case_id,
            disposition=disposition.value,
            reason_code=reason_code,
        )
        self.links.append(row)
        return EvidenceLinkResult(link=row, created=True)


def _captured_event() -> NormalizedEvent:
    return NormalizedEvent(
        id="event_001",
        merchant_id="merchant_001",
        webhook_event_id="webhook_001",
        schema_version="1.0",
        source="RAZORPAY",
        source_event_id="provider_event_001",
        event_type="payment.captured",
        occurred_at=NOW,
        received_at=NOW,
        customer_id="customer_001",
        payment_id="payment_001",
        order_id=None,
        subscription_id=None,
        invoice_id=None,
        payment_link_id=None,
        amount_minor=10_000,
        currency="INR",
        failure_code=None,
        normalized_failure_category="NONE",
        correlation_id="correlation_001",
        causation_id=None,
        source_payload_reference="webhook_events/provider_event_001",
        normalized_payload={},
    )


async def test_non_failure_evidence_is_linked_audit_only_without_case_or_effect() -> None:
    repository = AuditOnlyRepository(_captured_event())
    service = RecoveryApplicationService(
        cast(RecoveryRepository, repository),
        clock=lambda: NOW,
    )

    result = await service.process_event(
        merchant_id="merchant_001",
        normalized_event_id="event_001",
    )

    assert repository.locked_merchant == "merchant_001"
    assert result.case_id is None
    assert result.disposition is EvidenceDisposition.AUDIT_ONLY
    assert result.receipt_id is None
    assert repository.links[0].reason_code == "EVENT_HAS_NO_RECOVERY_DIAGNOSIS"


async def test_tenant_scoped_missing_event_fails_before_creating_any_link() -> None:
    repository = AuditOnlyRepository(None)
    service = RecoveryApplicationService(
        cast(RecoveryRepository, repository),
        clock=lambda: NOW,
    )

    with pytest.raises(LookupError, match="tenant-scoped normalized event"):
        await service.process_event(
            merchant_id="merchant_other",
            normalized_event_id="event_001",
        )
    assert repository.links == []
