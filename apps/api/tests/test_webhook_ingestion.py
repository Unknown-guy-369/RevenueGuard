from __future__ import annotations

import hashlib
import hmac
from dataclasses import fields

import pytest
from httpx import ASGITransport, AsyncClient
from revenueguard_api.config import Settings, get_settings
from revenueguard_api.main import create_app
from revenueguard_api.webhooks import (
    IngestionDisposition,
    InvalidSignatureRecord,
    ResolvedMerchant,
    VerifiedRazorpayWebhook,
    WebhookPersistenceError,
)

WEBHOOK_PATH = "/api/v1/webhooks/razorpay"
WEBHOOK_SECRET = "test-mode-webhook-secret"
ROUTING_IDENTIFIER = "route_test_merchant"
RAW_BODY = b'{\n  "event": "payment.failed", "merchant_id": "payload-is-untrusted"\n}\n'


class FakeMerchantResolver:
    def __init__(self, merchant: ResolvedMerchant | None) -> None:
        self.merchant = merchant
        self.routing_identifiers: list[str] = []

    async def resolve(self, routing_identifier: str) -> ResolvedMerchant | None:
        self.routing_identifiers.append(routing_identifier)
        return self.merchant


class FakeIngestionService:
    def __init__(
        self,
        disposition: IngestionDisposition = IngestionDisposition.ACCEPTED,
        failure: WebhookPersistenceError | None = None,
    ) -> None:
        self.disposition = disposition
        self.failure = failure
        self.verified: list[VerifiedRazorpayWebhook] = []
        self.invalid: list[InvalidSignatureRecord] = []

    async def ingest_verified(self, webhook: VerifiedRazorpayWebhook) -> IngestionDisposition:
        if self.failure is not None:
            raise self.failure
        self.verified.append(webhook)
        return self.disposition

    async def record_invalid_signature(self, record: InvalidSignatureRecord) -> None:
        if self.failure is not None:
            raise self.failure
        self.invalid.append(record)


def sign(body: bytes = RAW_BODY, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def webhook_headers(
    *,
    signature: str | None = None,
    event_id: str | None = "evt_unique_1",
    routing_identifier: str | None = ROUTING_IDENTIFIER,
    routing_header: str = "X-RevenueGuard-Merchant-Id",
) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Razorpay-Signature"] = signature
    if event_id is not None:
        headers["X-Razorpay-Event-Id"] = event_id
    if routing_identifier is not None:
        headers[routing_header] = routing_identifier
    return headers


def make_client(
    resolver: FakeMerchantResolver,
    ingestion: FakeIngestionService,
    settings: Settings | None = None,
) -> AsyncClient:
    get_settings.cache_clear()
    app = create_app(
        merchant_webhook_resolver=resolver,
        webhook_ingestion_service=ingestion,
    )
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def resolved_merchant() -> ResolvedMerchant:
    return ResolvedMerchant(merchant_id="merchant_internal_123", webhook_secret=WEBHOOK_SECRET)


async def test_accepts_exact_raw_body_and_uses_resolved_tenant(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(signature=sign()),
        )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "provider_event_id": "evt_unique_1"}
    assert resolver.routing_identifiers == [ROUTING_IDENTIFIER]
    assert len(ingestion.verified) == 1
    stored = ingestion.verified[0]
    assert stored.raw_body == RAW_BODY
    assert stored.payload["merchant_id"] == "payload-is-untrusted"
    assert stored.merchant_id == "merchant_internal_123"
    assert ingestion.invalid == []


async def test_missing_routing_header_uses_explicit_single_merchant_fallback(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()
    settings = Settings(razorpay_merchant_id="merchant_internal_123")

    async with make_client(resolver, ingestion, settings) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(
                signature=sign(),
                routing_identifier=None,
            ),
        )

    assert response.status_code == 202
    assert resolver.routing_identifiers == ["merchant_internal_123"]
    assert [webhook.merchant_id for webhook in ingestion.verified] == ["merchant_internal_123"]


async def test_single_merchant_fallback_still_requires_a_valid_signature(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()
    settings = Settings(razorpay_merchant_id="merchant_internal_123")

    async with make_client(resolver, ingestion, settings) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(
                signature="0" * 64,
                routing_identifier=None,
            ),
        )

    assert response.status_code == 401
    assert ingestion.verified == []
    assert len(ingestion.invalid) == 1
    assert ingestion.invalid[0].merchant_id == "merchant_internal_123"


async def test_supplied_wrong_route_never_uses_single_merchant_fallback() -> None:
    resolver = FakeMerchantResolver(None)
    ingestion = FakeIngestionService()
    settings = Settings(razorpay_merchant_id="merchant_internal_123")

    async with make_client(resolver, ingestion, settings) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(
                signature=sign(),
                routing_identifier="merchant_wrong",
            ),
        )

    assert response.status_code == 401
    assert resolver.routing_identifiers == ["merchant_wrong"]
    assert ingestion.verified == []
    assert ingestion.invalid == []


async def test_supplied_blank_route_never_uses_single_merchant_fallback(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()
    settings = Settings(razorpay_merchant_id="merchant_internal_123")
    headers = webhook_headers(signature=sign())
    headers["X-RevenueGuard-Merchant-Id"] = "   "

    async with make_client(resolver, ingestion, settings) as client:
        response = await client.post(WEBHOOK_PATH, content=RAW_BODY, headers=headers)

    assert response.status_code == 400
    assert response.json() == {"detail": "missing required header: X-RevenueGuard-Merchant-Id"}
    assert resolver.routing_identifiers == []
    assert ingestion.verified == []


async def test_returns_200_when_durable_inbox_reports_duplicate(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService(IngestionDisposition.DUPLICATE)

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(signature=sign()),
        )

    assert response.status_code == 200
    assert response.json() == {"status": "duplicate", "provider_event_id": "evt_unique_1"}
    assert len(ingestion.verified) == 1


async def test_invalid_signature_is_rejected_before_json_parsing_and_stores_hashes_only(
    resolved_merchant: ResolvedMerchant,
) -> None:
    malformed_json = b"not-json-at-all"
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=malformed_json,
            headers=webhook_headers(signature="0" * 64),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "webhook authentication failed"}
    assert ingestion.verified == []
    assert len(ingestion.invalid) == 1
    record = ingestion.invalid[0]
    assert record.merchant_id == "merchant_internal_123"
    assert record.payload_sha256 == hashlib.sha256(malformed_json).hexdigest()
    assert record.signature_sha256 == hashlib.sha256(("0" * 64).encode()).hexdigest()
    assert {item.name for item in fields(record)} == {
        "merchant_id",
        "provider_event_id",
        "payload_sha256",
        "signature_sha256",
        "received_at",
    }


async def test_malformed_non_hex_signature_is_an_authentication_failure(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(signature="not-a-hex-signature"),
        )

    assert response.status_code == 401
    assert ingestion.verified == []
    assert len(ingestion.invalid) == 1


async def test_valid_signature_with_non_object_json_is_rejected_without_persistence(
    resolved_merchant: ResolvedMerchant,
) -> None:
    body = b"[]"
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=body,
            headers=webhook_headers(signature=sign(body)),
        )

    assert response.status_code == 400
    assert ingestion.verified == []
    assert ingestion.invalid == []


@pytest.mark.parametrize(
    ("missing_header", "expected_name"),
    [
        ("X-Razorpay-Signature", "X-Razorpay-Signature"),
        ("X-Razorpay-Event-Id", "X-Razorpay-Event-Id"),
        ("X-RevenueGuard-Merchant-Id", "X-RevenueGuard-Merchant-Id"),
    ],
)
async def test_missing_or_blank_required_headers_return_400_without_side_effects(
    missing_header: str,
    expected_name: str,
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()
    headers = webhook_headers(signature=sign())
    headers[missing_header] = "   "

    async with make_client(resolver, ingestion) as client:
        response = await client.post(WEBHOOK_PATH, content=RAW_BODY, headers=headers)

    assert response.status_code == 400
    assert response.json() == {"detail": f"missing required header: {expected_name}"}
    assert ingestion.verified == []
    assert ingestion.invalid == []


async def test_body_over_configured_limit_is_rejected_before_tenant_lookup(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()
    settings = Settings(razorpay_webhook_max_body_bytes=8)

    async with make_client(resolver, ingestion, settings) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(signature=sign()),
        )

    assert response.status_code == 413
    assert resolver.routing_identifiers == []
    assert ingestion.verified == []


async def test_unknown_routing_identifier_fails_closed_without_persisting() -> None:
    resolver = FakeMerchantResolver(None)
    ingestion = FakeIngestionService()

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(signature=sign()),
        )

    assert response.status_code == 401
    assert ingestion.verified == []
    assert ingestion.invalid == []


async def test_configured_merchant_header_is_used_for_resolution(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()
    settings = Settings(razorpay_merchant_routing_header="X-Test-Merchant-Route")

    async with make_client(resolver, ingestion, settings) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(
                signature=sign(),
                routing_header="X-Test-Merchant-Route",
            ),
        )

    assert response.status_code == 202
    assert resolver.routing_identifiers == [ROUTING_IDENTIFIER]


async def test_durable_inbox_failure_does_not_acknowledge_event(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService(failure=WebhookPersistenceError("database unavailable"))

    async with make_client(resolver, ingestion) as client:
        response = await client.post(
            WEBHOOK_PATH,
            content=RAW_BODY,
            headers=webhook_headers(signature=sign()),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "durable webhook inbox unavailable"}
    assert ingestion.verified == []


async def test_openapi_documents_webhook_contract_and_phase_two_description(
    resolved_merchant: ResolvedMerchant,
) -> None:
    resolver = FakeMerchantResolver(resolved_merchant)
    ingestion = FakeIngestionService()

    async with make_client(resolver, ingestion) as client:
        document = (await client.get("/openapi.json")).json()

    assert "Phase 2" in document["info"]["description"]
    operation = document["paths"][WEBHOOK_PATH]["post"]
    header_names = {
        parameter["name"] for parameter in operation["parameters"] if parameter["in"] == "header"
    }
    assert {
        "X-Razorpay-Signature",
        "X-Razorpay-Event-Id",
        "X-RevenueGuard-Merchant-Id",
    }.issubset(header_names)
    assert {"200", "202", "401", "413", "503"}.issubset(operation["responses"])
