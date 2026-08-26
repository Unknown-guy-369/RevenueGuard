from __future__ import annotations

from datetime import UTC, datetime

import pytest
from revenueguard_domain import (
    ActionStatus,
    ActionType,
    RecoveryAction,
    SubjectType,
    action_idempotency_key,
)
from revenueguard_integrations.execution import (
    DeterministicSimulatorAdapter,
    HttpResponse,
    RazorpayTestModeAdapter,
)

NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)


def _action(**parameters: object) -> RecoveryAction:
    key = action_idempotency_key(
        merchant_id="merchant_001",
        case_id="case_001",
        action_type=ActionType.CREATE_PAYMENT_LINK,
        target_type=SubjectType.SUBSCRIPTION,
        target_id="subscription_001",
        logical_attempt=1,
    )
    return RecoveryAction(
        action_id="action_001",
        case_id="case_001",
        merchant_id="merchant_001",
        decision_receipt_id="receipt_001",
        action_type=ActionType.CREATE_PAYMENT_LINK,
        target_type=SubjectType.SUBSCRIPTION,
        target_id="subscription_001",
        logical_attempt=1,
        idempotency_key=key,
        status=ActionStatus.PENDING,
        parameters={
            "amount_minor": 10_000,
            "currency": "INR",
            "provider_mode": "TEST",
            **parameters,
        },
        authorized_at=NOW,
        execute_after=NOW,
        created_at=NOW,
    )


class FakeTransport:
    def __init__(self, response: HttpResponse | None = None, *, timeout: bool = False) -> None:
        self.response = response
        self.timeout = timeout
        self.calls = 0

    async def request(self, **_: object) -> HttpResponse:
        self.calls += 1
        if self.timeout:
            raise TimeoutError
        assert self.response is not None
        return self.response


async def test_simulator_is_deterministic_and_labels_ambiguous_results() -> None:
    adapter = DeterministicSimulatorAdapter()
    first = await adapter.execute(_action())
    second = await adapter.execute(_action())
    assert first.status is ActionStatus.SUCCEEDED
    assert first.provider_object_id == second.provider_object_id
    unknown = await adapter.execute(_action(simulation_outcome="TIMEOUT"))
    assert unknown.status is ActionStatus.UNKNOWN
    assert not unknown.retryable
    simulated_success = await adapter.lookup(_action(simulation_lookup_outcome="SUCCEEDED"))
    assert simulated_success.status is ActionStatus.SUCCEEDED
    assert not simulated_success.is_authoritative


def test_razorpay_adapter_refuses_live_or_unclassified_credentials() -> None:
    with pytest.raises(ValueError, match="rzp_test_"):
        RazorpayTestModeAdapter(key_id="rzp_live_secret", key_secret="secret")


async def test_razorpay_timeout_is_unknown_and_never_blindly_retryable() -> None:
    transport = FakeTransport(timeout=True)
    adapter = RazorpayTestModeAdapter(
        key_id="rzp_test_example",
        key_secret="secret",
        transport=transport,
    )
    result = await adapter.execute(_action())
    assert result.status is ActionStatus.UNKNOWN
    assert not result.retryable
    assert transport.calls == 1


async def test_razorpay_definite_rejection_can_fail_without_recovery_credit() -> None:
    transport = FakeTransport(
        HttpResponse(status_code=400, body=b"{}", request_reference="razorpay/payment_links")
    )
    result = await RazorpayTestModeAdapter(
        key_id="rzp_test_example",
        key_secret="secret",
        transport=transport,
    ).execute(_action())
    assert result.status is ActionStatus.FAILED
    assert result.error_code == "PROVIDER_REJECTED"


async def test_razorpay_can_recover_provider_id_by_stable_reference_after_worker_loss() -> None:
    reference_id = _action().idempotency_key[-40:]
    transport = FakeTransport(
        HttpResponse(
            status_code=200,
            body=(
                '{"payment_links":[{"id":"plink_recovered","reference_id":"'
                + reference_id
                + '","status":"created"}]}'
            ).encode(),
            request_reference=f"razorpay/payment_links?reference_id={reference_id}",
        )
    )
    result = await RazorpayTestModeAdapter(
        key_id="rzp_test_example",
        key_secret="secret",
        transport=transport,
    ).lookup(_action())
    assert result.status is ActionStatus.PENDING
    assert result.provider_object_id == "plink_recovered"
    assert result.is_authoritative
