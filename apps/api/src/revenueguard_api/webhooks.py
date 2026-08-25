"""Typed application boundary for Razorpay webhook ingestion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from revenueguard_integrations.razorpay import verify_webhook_signature


@dataclass(frozen=True, slots=True)
class ResolvedMerchant:
    """An internally resolved merchant and its tenant-specific webhook secret."""

    merchant_id: str
    webhook_secret: str = field(repr=False)


class MerchantWebhookResolver(Protocol):
    """Resolve an opaque routing identifier inside the authoritative merchant scope."""

    async def resolve(self, routing_identifier: str) -> ResolvedMerchant | None: ...


class IngestionDisposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class VerifiedRazorpayWebhook:
    """A signature-verified webhook ready for one transactional inbox insert."""

    merchant_id: str
    provider_event_id: str
    raw_body: bytes = field(repr=False)
    payload: dict[str, Any] = field(repr=False)
    received_at: datetime


@dataclass(frozen=True, slots=True)
class InvalidSignatureRecord:
    """Safe audit metadata for a rejected webhook; raw input is intentionally absent."""

    merchant_id: str
    provider_event_id: str
    payload_sha256: str
    signature_sha256: str
    received_at: datetime


class WebhookIngestionService(Protocol):
    """Durably write verified inbox events and rejected-signature audit metadata."""

    async def ingest_verified(self, webhook: VerifiedRazorpayWebhook) -> IngestionDisposition: ...

    async def record_invalid_signature(self, record: InvalidSignatureRecord) -> None: ...


class WebhookPersistenceError(RuntimeError):
    """The durable webhook store could not commit the requested record."""


class UnconfiguredMerchantResolver:
    """Safe default used until the composition root installs a database resolver."""

    async def resolve(self, routing_identifier: str) -> ResolvedMerchant | None:
        del routing_identifier
        return None


class UnconfiguredWebhookIngestionService:
    """Fail closed when persistence has not been wired into the process."""

    async def ingest_verified(self, webhook: VerifiedRazorpayWebhook) -> IngestionDisposition:
        del webhook
        raise WebhookPersistenceError("webhook persistence is not configured")

    async def record_invalid_signature(self, record: InvalidSignatureRecord) -> None:
        del record
        raise WebhookPersistenceError("webhook persistence is not configured")


def verify_razorpay_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify Razorpay's HMAC-SHA256 signature over the exact received bytes."""

    return verify_webhook_signature(raw_body, signature.strip(), secret.encode("utf-8"))


def sha256_hex(value: bytes) -> str:
    """Return a lowercase SHA-256 digest suitable for safe audit correlation."""

    return hashlib.sha256(value).hexdigest()
