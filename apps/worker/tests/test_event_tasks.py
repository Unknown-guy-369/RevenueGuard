from __future__ import annotations

from datetime import UTC, datetime
from typing import Never

import pytest
from revenueguard_integrations.razorpay import UnsupportedRazorpayEventError
from revenueguard_worker import tasks
from revenueguard_worker.celery_app import celery_app


def test_phase2_tasks_are_registered_with_dedicated_queues() -> None:
    assert "revenueguard.events.dispatch_pending" in celery_app.tasks
    assert "revenueguard.events.process" in celery_app.tasks
    assert celery_app.conf.task_routes["revenueguard.events.dispatch_pending"] == {
        "queue": "event_dispatch"
    }
    assert celery_app.conf.task_routes["revenueguard.events.process"] == {
        "queue": "event_ingestion"
    }


def test_event_correlations_are_typed_and_only_persist_present_ids() -> None:
    correlations = tasks._event_correlations(
        {
            "customer_id": "cust_001",
            "payment_id": "pay_001",
            "order_id": "order_001",
            "subscription_id": None,
            "invoice_id": None,
            "payment_link_id": None,
        }
    )

    assert correlations == (
        {
            "reference_type": "CUSTOMER",
            "external_id": "cust_001",
            "internal_id": "cust_001",
        },
        {
            "reference_type": "PAYMENT",
            "external_id": "pay_001",
            "internal_id": "pay_001",
        },
        {
            "reference_type": "ORDER",
            "external_id": "order_001",
            "internal_id": None,
        },
    )


def test_provider_timestamp_falls_back_safely() -> None:
    assert tasks._provider_timestamp({"created_at": "not-an-integer"}, "created_at") is None
    assert tasks._provider_timestamp({"created_at": True}, "created_at") is None
    assert tasks._provider_timestamp({"created_at": -1}, "created_at") is None
    assert tasks._provider_timestamp({"created_at": 1787632259}, "created_at") == (
        datetime.fromtimestamp(1787632259, UTC)
    )


def test_unsupported_event_is_dead_lettered_without_repeat_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, str, bool]] = []

    async def reject(*_: str) -> Never:
        raise UnsupportedRazorpayEventError("untrusted.event")

    async def record(
        dispatch_id: str,
        *,
        error_code: str,
        error_detail: str,
        terminal: bool,
    ) -> str:
        del error_detail
        recorded.append((dispatch_id, error_code, terminal))
        return "DEAD_LETTER"

    monkeypatch.setattr(tasks, "_process_webhook_event", reject)
    monkeypatch.setattr(tasks, "_record_processing_failure", record)

    result = tasks.process_webhook_event.run("dispatch_001", "merchant_001", "webhook_001")

    assert result == {"dispatch_id": "dispatch_001", "status": "dead_letter"}
    assert recorded == [("dispatch_001", "UNSUPPORTED_RAZORPAY_EVENT", True)]


def test_transient_failure_is_retried_through_durable_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*_: str) -> Never:
        raise RuntimeError("does not include provider data")

    async def record(
        dispatch_id: str,
        *,
        error_code: str,
        error_detail: str,
        terminal: bool,
    ) -> str:
        assert dispatch_id == "dispatch_002"
        assert error_code == "TRANSIENT_PROCESSING_ERROR"
        assert error_detail == "transient worker processing failure"
        assert terminal is False
        return "RETRY_SCHEDULED"

    monkeypatch.setattr(tasks, "_process_webhook_event", fail)
    monkeypatch.setattr(tasks, "_record_processing_failure", record)

    result = tasks.process_webhook_event.run("dispatch_002", "merchant_001", "webhook_002")

    assert result == {"dispatch_id": "dispatch_002", "status": "retry_scheduled"}
