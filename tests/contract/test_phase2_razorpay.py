from __future__ import annotations

import copy
import hashlib
import hmac
import json
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from revenueguard_domain.events import EventSource, NormalizedFailureCategory
from revenueguard_integrations.razorpay import (
    SUPPORTED_EVENT_TYPES,
    MalformedRazorpayEventError,
    UnsupportedRazorpayEventError,
    normalize_razorpay_event,
    verify_webhook_signature,
)

from tests.contract.test_phase0_contracts import load_json, validate_json_schema_subset

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "razorpay"
EVENT_SCHEMA = ROOT / "docs" / "contracts" / "v1" / "revenue-risk-event.schema.json"


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _raw_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _mutable_fixture(name: str = "payment_failed.json") -> dict[str, Any]:
    return copy.deepcopy(load_json(FIXTURES / name))


def _normalize_document(document: dict[str, Any], **overrides: object) -> object:
    arguments: dict[str, object] = {
        "merchant_id": "merchant_from_verified_route",
        "provider_event_id": "provider_event_test",
        "event_id": "internal_event_test",
        "received_at": _parse_datetime("2026-08-25T05:00:00Z"),
        "correlation_id": "correlation_test",
        "source_payload_reference": "webhook_events/provider_event_test",
    }
    arguments.update(overrides)
    return normalize_razorpay_event(
        json.dumps(document, separators=(",", ":")).encode(),
        **arguments,  # type: ignore[arg-type]
    )


class RazorpaySignatureContractTests(unittest.TestCase):
    def test_signature_is_hmac_sha256_of_exact_raw_body(self) -> None:
        raw_body = _raw_fixture("payment_failed.json")
        secret = b"fixture-only-webhook-secret"
        signature = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

        self.assertTrue(verify_webhook_signature(raw_body, signature, secret))
        self.assertTrue(verify_webhook_signature(raw_body, signature.upper(), secret))

        reserialized = json.dumps(json.loads(raw_body), separators=(",", ":")).encode()
        self.assertNotEqual(raw_body, reserialized)
        self.assertFalse(verify_webhook_signature(reserialized, signature, secret))

    def test_signature_verification_fails_closed(self) -> None:
        raw_body = b'{"entity":"event"}'
        secret = b"fixture-only-webhook-secret"

        self.assertFalse(verify_webhook_signature(raw_body, None, secret))
        self.assertFalse(verify_webhook_signature(raw_body, "", secret))
        self.assertFalse(verify_webhook_signature(raw_body, "not-hex", secret))
        self.assertFalse(verify_webhook_signature(raw_body, "0" * 64, secret))

    def test_signature_api_rejects_non_bytes_and_empty_secrets(self) -> None:
        with self.assertRaises(TypeError):
            verify_webhook_signature("{}", "0" * 64, b"secret")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            verify_webhook_signature(b"{}", "0" * 64, b"")


class RazorpayNormalizationContractTests(unittest.TestCase):
    def test_fixture_manifest_and_snapshots_are_explicitly_synthetic(self) -> None:
        manifest = load_json(FIXTURES / "manifest.json")
        self.assertEqual(manifest["classification"], "SYNTHETIC")
        self.assertIn("not production", manifest["disclaimer"])

        schema = load_json(EVENT_SCHEMA)
        for entry in manifest["fixtures"]:
            with self.subTest(payload=entry["payload"]):
                raw_body = _raw_fixture(entry["payload"])
                normalized = normalize_razorpay_event(
                    raw_body,
                    merchant_id=entry["merchant_id"],
                    provider_event_id=entry["provider_event_id"],
                    event_id=entry["event_id"],
                    received_at=_parse_datetime(entry["received_at"]),
                    correlation_id=entry["correlation_id"],
                    causation_id=entry["causation_id"],
                    source_payload_reference=entry["source_payload_reference"],
                    source=EventSource.SYNTHETIC,
                )
                actual = normalized.to_dict()
                expected = load_json(FIXTURES / entry["snapshot"])
                self.assertEqual(actual, expected)
                self.assertEqual(actual["source"], "SYNTHETIC")
                validate_json_schema_subset(actual, schema, schema)

    def test_supported_event_allowlist_is_explicit_and_closed(self) -> None:
        self.assertEqual(
            SUPPORTED_EVENT_TYPES,
            {
                "payment.authorized",
                "payment.captured",
                "payment.failed",
                "subscription.pending",
                "subscription.charged",
                "subscription.halted",
                "payment_link.paid",
                "payment_link.cancelled",
                "payment_link.expired",
            },
        )

    def test_supported_success_and_terminal_events_have_no_failure(self) -> None:
        variants = (
            ("payment_failed.json", "payment.authorized"),
            ("payment_failed.json", "payment.captured"),
            ("subscription_pending.json", "subscription.charged"),
            ("payment_link_paid.json", "payment_link.cancelled"),
            ("payment_link_paid.json", "payment_link.expired"),
        )
        for fixture, event_type in variants:
            with self.subTest(event_type=event_type):
                document = _mutable_fixture(fixture)
                document["event"] = event_type
                event = _normalize_document(document)
                self.assertIsNone(event.failure_code)
                self.assertEqual(
                    event.normalized_failure_category,
                    NormalizedFailureCategory.NONE,
                )

    def test_payment_link_entity_declaration_may_be_omitted_by_provider(self) -> None:
        document = _mutable_fixture("payment_link_paid.json")
        payment_link = document["payload"]["payment_link"]["entity"]
        payment_link.pop("entity")

        event = _normalize_document(document)

        self.assertEqual(event.payment_link_id, "plink_fixture_001")
        self.assertEqual(event.amount_minor, 1_250_000)
        self.assertEqual(event.currency, "INR")

    def test_explicit_wrong_payment_link_entity_declaration_is_rejected(self) -> None:
        document = _mutable_fixture("payment_link_paid.json")
        document["payload"]["payment_link"]["entity"]["entity"] = "invoice"

        with self.assertRaises(MalformedRazorpayEventError):
            _normalize_document(document)

    def test_merchant_is_resolved_externally_not_from_provider_account_id(self) -> None:
        document = _mutable_fixture()
        document["account_id"] = "merchant_attacker_selected"
        event = _normalize_document(document)

        self.assertEqual(event.merchant_id, "merchant_from_verified_route")
        self.assertNotEqual(event.merchant_id, document["account_id"])

    def test_failure_reason_mapping_is_provider_neutral(self) -> None:
        cases = {
            "insufficient_funds": NormalizedFailureCategory.INSUFFICIENT_FUNDS,
            "expired_card": NormalizedFailureCategory.EXPIRED_PAYMENT_METHOD,
            "authentication_failed": NormalizedFailureCategory.AUTHENTICATION_FAILURE,
            "issuer_unavailable": NormalizedFailureCategory.ISSUER_UNAVAILABLE,
            "gateway_unavailable": NormalizedFailureCategory.GATEWAY_UNAVAILABLE,
            "customer_action_required": NormalizedFailureCategory.CUSTOMER_ACTION_REQUIRED,
            "dispute_created": NormalizedFailureCategory.DISPUTE,
            "unmapped_provider_reason": NormalizedFailureCategory.UNKNOWN,
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                document = _mutable_fixture()
                payment = document["payload"]["payment"]["entity"]
                payment["error_code"] = "PROVIDER_ERROR"
                payment["error_reason"] = reason
                payment["error_description"] = "Provider supplied failure."
                event = _normalize_document(document)
                self.assertEqual(event.normalized_failure_category, expected)
                self.assertEqual(event.failure_code, "PROVIDER_ERROR")

    def test_unsupported_events_fail_safely_with_machine_readable_code(self) -> None:
        document = _mutable_fixture()
        document["event"] = "refund.processed"

        with self.assertRaises(UnsupportedRazorpayEventError) as context:
            _normalize_document(document)
        self.assertEqual(context.exception.code, "UNSUPPORTED_RAZORPAY_EVENT")
        self.assertEqual(context.exception.event_type, "refund.processed")

    def test_malformed_payloads_fail_safely(self) -> None:
        cases: list[tuple[str, bytes]] = [
            ("invalid JSON", b"{"),
            ("non-object JSON", b"[]"),
            ("non-UTF-8", b"\xff"),
        ]
        for label, raw_body in cases:
            with (
                self.subTest(case=label),
                self.assertRaises(MalformedRazorpayEventError) as context,
            ):
                normalize_razorpay_event(
                    raw_body,
                    merchant_id="merchant_test",
                    provider_event_id="provider_event_test",
                    event_id="event_test",
                    received_at=_parse_datetime("2026-08-25T05:00:00Z"),
                    correlation_id="correlation_test",
                    source_payload_reference="webhook_events/provider_event_test",
                )
            self.assertEqual(context.exception.code, "MALFORMED_RAZORPAY_EVENT")

    def test_invalid_entity_amount_currency_and_timestamp_are_rejected(self) -> None:
        mutations = {
            "missing entity": lambda document: document["payload"].pop("payment"),
            "float amount": lambda document: document["payload"]["payment"]["entity"].update(
                {"amount": 4999.0}
            ),
            "negative amount": lambda document: document["payload"]["payment"]["entity"].update(
                {"amount": -1}
            ),
            "lowercase currency": lambda document: document["payload"]["payment"]["entity"].update(
                {"currency": "inr"}
            ),
            "wrong entity declaration": lambda document: document["payload"]["payment"][
                "entity"
            ].update({"entity": "refund"}),
            "boolean timestamp": lambda document: document.update({"created_at": True}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                document = _mutable_fixture()
                mutate(document)
                with self.assertRaises(MalformedRazorpayEventError):
                    _normalize_document(document)

    def test_normalized_time_is_timezone_aware_utc(self) -> None:
        event = _normalize_document(_mutable_fixture())
        self.assertEqual(event.occurred_at.utcoffset().total_seconds(), 0)
        self.assertEqual(event.received_at.utcoffset().total_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
