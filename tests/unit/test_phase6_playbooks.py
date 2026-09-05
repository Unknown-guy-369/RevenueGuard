from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

import pytest
from revenueguard_domain import (
    ActionType,
    CandidateAction,
    ConsentState,
    ContactChannel,
    DegradationPolicy,
    PaymentOutcomeObservation,
    PolicyEvaluationInput,
    PolicyResult,
    PromiseExtraction,
    PromiseIntent,
    PromiseStatus,
    assess_payment_degradation,
    conservative_default_policy,
    create_promise,
    evaluate_policy,
)
from revenueguard_integrations.playbooks import BoundedPromiseExtractor

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


class StructuredProvider:
    @property
    def model_version(self) -> str:
        return "promise-extractor-test-1"

    async def extract(self, *, text: str, max_output_tokens: int) -> Mapping[str, object]:
        assert text == "I will pay on Monday"
        assert max_output_tokens == 200
        return {
            "intent": "PROMISE_TO_PAY",
            "promised_date": "2026-08-31",
            "amount_minor": 7_500,
            "currency": "INR",
            "confidence_basis_points": 9_200,
        }


class MalformedProvider:
    @property
    def model_version(self) -> str:
        return "malformed-test-1"

    async def extract(self, *, text: str, max_output_tokens: int) -> Mapping[str, object]:
        del text, max_output_tokens
        return {"intent": "PROMISE_TO_PAY", "invented_authority": True}


class SlowProvider:
    @property
    def model_version(self) -> str:
        return "slow-test-1"

    async def extract(self, *, text: str, max_output_tokens: int) -> Mapping[str, object]:
        del text, max_output_tokens
        await asyncio.sleep(0.05)
        return {"intent": "UNKNOWN", "confidence_basis_points": 0}


async def test_bounded_promise_extraction_accepts_only_the_typed_schema() -> None:
    extraction = await BoundedPromiseExtractor(StructuredProvider()).extract("I will pay on Monday")

    assert extraction == PromiseExtraction(
        intent=PromiseIntent.PROMISE_TO_PAY,
        promised_date=date(2026, 8, 31),
        amount_minor=7_500,
        currency="INR",
        confidence_basis_points=9_200,
        extractor_version="promise-extractor-test-1",
    )


@pytest.mark.parametrize(
    "extractor",
    (
        BoundedPromiseExtractor(MalformedProvider()),
        BoundedPromiseExtractor(SlowProvider(), timeout_seconds=0.001),
        BoundedPromiseExtractor(),
    ),
)
async def test_malformed_slow_or_unavailable_extraction_falls_back_safely(
    extractor: BoundedPromiseExtractor,
) -> None:
    extraction = await extractor.extract("untrusted free-form response")

    assert extraction.intent is PromiseIntent.UNKNOWN
    assert extraction.confidence_basis_points == 0
    assert extraction.extractor_version.startswith("phase6-deterministic-fallback:")


def test_promise_terms_preserve_invoice_money_and_schedule_durably() -> None:
    promise = create_promise(
        promise_id="promise_001",
        merchant_id="merchant_001",
        case_id="case_001",
        invoice_id="invoice_001",
        customer_id="customer_001",
        outstanding_amount_minor=10_000,
        invoice_currency="INR",
        extraction=PromiseExtraction(
            intent=PromiseIntent.PROMISE_TO_PAY,
            promised_date=date(2026, 8, 31),
            amount_minor=7_500,
            currency="INR",
            confidence_basis_points=9_200,
            extractor_version="extractor-1",
        ),
        source_response_id="response_001",
        received_at=NOW,
    )

    assert promise.status is PromiseStatus.ACTIVE
    assert promise.amount_minor == 7_500
    assert promise.currency == "INR"
    assert promise.reminder_at == datetime(2026, 8, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("amount_minor", "currency"),
    ((10_001, "INR"), (7_500, "USD")),
)
def test_promise_cannot_change_invoice_money_terms(amount_minor: int, currency: str) -> None:
    with pytest.raises(ValueError):
        create_promise(
            promise_id="promise_001",
            merchant_id="merchant_001",
            case_id="case_001",
            invoice_id="invoice_001",
            customer_id="customer_001",
            outstanding_amount_minor=10_000,
            invoice_currency="INR",
            extraction=PromiseExtraction(
                intent=PromiseIntent.PROMISE_TO_PAY,
                promised_date=date(2026, 8, 31),
                amount_minor=amount_minor,
                currency=currency,
                confidence_basis_points=9_200,
                extractor_version="extractor-1",
            ),
            source_response_id="response_001",
            received_at=NOW,
        )


def test_transparent_degradation_baseline_detects_only_the_spiking_dimension() -> None:
    observations: list[PaymentOutcomeObservation] = []
    for index in range(20):
        observations.append(
            _observation(
                index=index,
                occurred_at=NOW - timedelta(hours=1, minutes=index),
                failed=index < 2,
                issuer="BANK_A",
            )
        )
    for index in range(10):
        observations.append(
            _observation(
                index=100 + index,
                occurred_at=NOW - timedelta(minutes=index),
                failed=index < 8,
                issuer="BANK_A",
            )
        )
    for index in range(20):
        observations.append(
            _observation(
                index=200 + index,
                occurred_at=NOW - timedelta(hours=1, minutes=index),
                failed=index < 2,
                issuer="BANK_B",
            )
        )
    for index in range(10):
        observations.append(
            _observation(
                index=300 + index,
                occurred_at=NOW - timedelta(minutes=index),
                failed=index < 1,
                issuer="BANK_B",
            )
        )

    assessments = assess_payment_degradation(
        tuple(observations),
        evaluated_at=NOW,
        policy=DegradationPolicy(),
    )

    by_issuer = {assessment.issuer_family: assessment for assessment in assessments}
    assert by_issuer["BANK_A"].degraded is True
    assert by_issuer["BANK_A"].baseline_failure_rate_basis_points == 1_000
    assert by_issuer["BANK_A"].current_failure_rate_basis_points == 8_000
    assert by_issuer["BANK_B"].degraded is False


def test_successes_without_failure_family_remain_in_incident_rate_denominator() -> None:
    observations = []
    for index in range(20):
        failed = index < 2
        observations.append(
            PaymentOutcomeObservation(
                observation_id=f"baseline-real-{index}",
                merchant_id="merchant_001",
                payment_id=f"baseline-payment-{index}",
                succeeded=not failed,
                payment_method="UPI",
                issuer_family="BANK_A",
                error_family="ISSUER_UNAVAILABLE" if failed else "NONE",
                occurred_at=NOW - timedelta(hours=1, minutes=index),
            )
        )
    for index in range(10):
        failed = index < 8
        observations.append(
            PaymentOutcomeObservation(
                observation_id=f"current-real-{index}",
                merchant_id="merchant_001",
                payment_id=f"current-payment-{index}",
                succeeded=not failed,
                payment_method="UPI",
                issuer_family="BANK_A",
                error_family="ISSUER_UNAVAILABLE" if failed else "NONE",
                occurred_at=NOW - timedelta(minutes=index),
            )
        )

    assessment = assess_payment_degradation(
        tuple(observations),
        evaluated_at=NOW,
        policy=DegradationPolicy(),
    )[0]

    assert assessment.error_family == "ISSUER_UNAVAILABLE"
    assert assessment.baseline_total == 20
    assert assessment.current_total == 10
    assert assessment.degraded is True


def test_active_promise_defers_previously_authorized_customer_contact() -> None:
    decision = evaluate_policy(
        conservative_default_policy(),
        PolicyEvaluationInput(
            case_id="case_001",
            amount_minor=10_000,
            currency="INR",
            confidence_basis_points=9_000,
            retry_count=0,
            contact_count=0,
            evaluated_at=NOW,
            candidates=(
                CandidateAction(
                    action_type=ActionType.SEND_REMINDER,
                    recovery_probability_basis_points=7_000,
                    expected_net_recovery_minor=6_000,
                    rank=1,
                    target="invoice_001",
                    channel=ContactChannel.EMAIL,
                ),
                CandidateAction(
                    action_type=ActionType.NO_ACTION,
                    recovery_probability_basis_points=0,
                    expected_net_recovery_minor=0,
                    rank=2,
                    target="invoice_001",
                ),
            ),
            evidence_references=("event_001",),
            consent_by_channel=((ContactChannel.EMAIL, ConsentState.GRANTED),),
            active_promise_to_pay=True,
            promise_due_at=NOW + timedelta(days=3),
        ),
    )

    assert decision.result is PolicyResult.DEFER
    assert decision.reason_codes == ("ACTIVE_PROMISE_TO_PAY",)
    assert decision.next_evaluation_at == NOW + timedelta(days=3)


def _observation(
    *, index: int, occurred_at: datetime, failed: bool, issuer: str
) -> PaymentOutcomeObservation:
    return PaymentOutcomeObservation(
        observation_id=f"observation_{index}",
        merchant_id="merchant_001",
        payment_id=f"payment_{index}",
        succeeded=not failed,
        payment_method="UPI",
        issuer_family=issuer,
        error_family="ISSUER_UNAVAILABLE",
        occurred_at=occurred_at,
    )
