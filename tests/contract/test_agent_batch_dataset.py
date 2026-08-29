from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from revenueguard_domain import EventSource, diagnose_event, select_case_identity
from revenueguard_evaluation.webhook_replay import load_fixture_dataset, load_fixtures
from revenueguard_integrations.razorpay import normalize_razorpay_event

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "fixtures" / "razorpay" / "datasets" / "agent_batch_10_v1" / "dataset.json"


def test_agent_batch_manifest_matches_normalized_case_intelligence_inputs() -> None:
    document: dict[str, Any] = json.loads(DATASET.read_text(encoding="utf-8"))
    fixture_paths = load_fixture_dataset(DATASET)
    deliveries = load_fixtures(fixture_paths)

    assert document["classification"] == "SYNTHETIC"
    assert len(deliveries) == 10
    assert len({delivery.provider_event_id for delivery in deliveries}) == 10

    for index, (entry, delivery) in enumerate(
        zip(document["fixtures"], deliveries, strict=True),
        start=1,
    ):
        expected = entry["expected"]
        event = normalize_razorpay_event(
            delivery.raw_body,
            merchant_id="merchant_demo_001",
            provider_event_id=delivery.provider_event_id,
            event_id=f"dataset_event_{index:02d}",
            received_at=datetime(2026, 8, 28, 8, index, tzinfo=UTC),
            correlation_id=f"dataset_correlation_{index:02d}",
            source_payload_reference=f"synthetic/{delivery.provider_event_id}",
            source=EventSource.SYNTHETIC,
        )
        identity = select_case_identity(event)
        diagnosis = diagnose_event(event)

        assert identity is not None
        assert diagnosis is not None
        assert event.event_type == expected["event_type"]
        assert event.normalized_failure_category.value == expected["failure_category"]
        assert identity.workflow_type.value == expected["workflow_type"]
        assert identity.subject_type.value == expected["subject_type"]
        assert diagnosis.code == expected["diagnosis_code"]
        assert [candidate.action_type.value for candidate in diagnosis.candidates] == expected[
            "candidate_action_types"
        ]
