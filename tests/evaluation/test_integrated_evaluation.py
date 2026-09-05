from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from revenueguard_evaluation.batch import load_held_out_manifest
from revenueguard_evaluation.integrated import INTEGRATED_SCENARIOS, _build_report

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "fixtures" / "evaluation" / "held_out_v1" / "manifest.json"


def test_integrated_report_counts_only_authoritative_recovery() -> None:
    manifest = load_held_out_manifest(MANIFEST)
    sessions = [
        (scenario, f"sim_{index}", f"evt_{index}")
        for index, scenario in enumerate(INTEGRATED_SCENARIOS)
    ]
    snapshots = {}
    details = {}
    for scenario, simulation_id, _ in sessions:
        recovered = scenario.amount_minor if scenario.expected_state == "RECOVERED" else 0
        case_id = None if scenario.expected_state is None else f"case_{simulation_id}"
        snapshots[simulation_id] = {
            "status": "COMPLETED"
            if scenario.expected_state in {None, "RECOVERED"}
            else "PROCESSING",
            "case_id": case_id,
            "case_state": scenario.expected_state,
            "policy_result": "PROCEED" if scenario.expected_state == "RECOVERED" else None,
            "policy_reason_codes": ["POLICY_AUTHORIZED"],
            "action_type": "CREATE_PAYMENT_LINK"
            if scenario.expected_state == "RECOVERED"
            else None,
            "action_status": "SUCCEEDED" if scenario.expected_state == "RECOVERED" else None,
            "outcome_authoritative": scenario.expected_state == "RECOVERED",
            "recovered_amount_minor": recovered,
        }
        if case_id:
            details[case_id] = {
                "transitions": [{"id": "transition"}],
                "decisions": [{"id": "decision"}],
                "actions": (
                    [{"attempt_count": 1, "max_attempts": 3}]
                    if scenario.expected_state == "RECOVERED"
                    else []
                ),
                "outcomes": ([{"id": "outcome"}] if recovered else []),
            }

    report = _build_report(
        manifest=manifest,
        readiness={"status": "ready"},
        sessions=sessions,
        snapshots=snapshots,
        case_details=details,
        duplicate_event_id="evt_0",
        started_at=datetime(2026, 9, 5, tzinfo=UTC),
        completed_at=datetime(2026, 9, 5, 0, 1, tzinfo=UTC),
    )

    assert report["source"] == "SYNTHETIC"
    assert report["evaluation_scope"] == "LIVE_LOCAL_INTEGRATION_BATCH"
    assert report["batch"] == {
        "case_count": 8,
        "currency": "INR",
        "revenue_at_risk_minor": 201_700,
        "verified_gross_recovered_minor": 75_700,
        "recovery_rate_basis_points": 3_753,
        "recovered_case_count": 3,
        "human_review_case_count": 1,
        "deferred_case_count": 3,
    }
    assert report["idempotency"]["same_logical_event"] is True
    assert report["safety_gates"]["status"] == "PASS"
    assert report["result"] == "PASS"
