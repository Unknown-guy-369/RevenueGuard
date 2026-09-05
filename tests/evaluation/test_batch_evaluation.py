from __future__ import annotations

import json
from pathlib import Path

import pytest
from revenueguard_evaluation.batch import (
    EvaluationStrategy,
    load_held_out_manifest,
    run_batch_evaluation,
)
from revenueguard_evaluation.cli import main

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "fixtures" / "evaluation" / "held_out_v1" / "manifest.json"


def test_sealed_manifest_is_validated_before_evaluation() -> None:
    manifest = load_held_out_manifest(MANIFEST)

    assert manifest.dataset_version == "held_out_v1"
    assert manifest.scenario_count == 29
    assert manifest.content_hash == (
        "410c98c107148861ce42999e8ba0ff45c508c7bc5be9f0526ff527435c0d84d3"
    )
    assert manifest.coverage == {
        "coordination": 2,
        "execution": 3,
        "ingestion": 5,
        "llm_boundary": 5,
        "policy": 6,
        "portfolio": 3,
        "recovery": 4,
        "tenancy": 1,
    }


def test_manifest_tampering_is_rejected(tmp_path: Path) -> None:
    copied = tmp_path / "held_out_v1"
    copied.mkdir()
    manifest_document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_document["content_hash"] = "0" * 64
    (copied / "manifest.json").write_text(
        json.dumps(manifest_document),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="seal"):
        load_held_out_manifest(copied / "manifest.json")


def test_batch_report_is_reproducible_and_compares_all_frozen_strategies() -> None:
    first = run_batch_evaluation(MANIFEST, seeds=(101, 202), cases_per_seed=60)
    second = run_batch_evaluation(MANIFEST, seeds=(101, 202), cases_per_seed=60)

    assert first.to_dict() == second.to_dict()
    document = first.to_dict()
    assert document["source"] == "SYNTHETIC"
    assert document["evaluation_scope"] == "OFFLINE_STRATEGY_SIMULATION"
    assert document["dataset"]["scenario_count"] == 29
    assert document["simulation"]["seeds"] == [101, 202]
    assert document["simulation"]["cases_per_seed"] == 60
    assert set(document["strategies"]) == {strategy.value for strategy in EvaluationStrategy}
    assert document["safety_gates"]["policy_violations"] == {
        "status": "PASS",
        "count": 0,
    }
    assert document["safety_gates"]["unverified_amount_counted_as_recovered"] == {
        "status": "PASS",
        "count": 0,
    }
    assert document["safety_gates"]["cross_merchant_data_access"]["status"] == ("NOT_EVALUATED")
    assert document["safety_gates"]["accepted_valid_events_silently_lost"]["status"] == (
        "NOT_EVALUATED"
    )
    assert document["scenario_contract_validation"]["status"] == "PASS"
    assert document["scenario_contract_validation"]["execution_status"] == "NOT_EXECUTED"
    assert document["model_metrics"]["status"] == "NOT_EVALUATED"

    no_action = document["strategies"]["NO_ACTION"]["aggregate"]
    assert no_action["actions_attempted_total"] == 0
    assert no_action["verified_gross_recovered_minor_total"] == 0
    assert no_action["recovery_cost_minor_total"] == 0
    assert no_action["verified_net_recovered_minor_total"] == 0
    assert all(
        result["aggregate"]["cases_evaluated_total"] == 120
        for result in document["strategies"].values()
    )

    full_net = document["strategies"]["REVENUEGUARD_FULL"]["aggregate"][
        "verified_net_recovered_minor_mean"
    ]
    best_static_net = max(
        document["strategies"][name]["aggregate"]["verified_net_recovered_minor_mean"]
        for name in ("IMMEDIATE_STATIC_RETRY", "FIXED_DELAY_RETRY")
    )
    assert document["primary_success_criterion"]["best_static_baseline"] in {
        "IMMEDIATE_STATIC_RETRY",
        "FIXED_DELAY_RETRY",
    }
    assert document["primary_success_criterion"]["passed"] is (full_net > best_static_net)


def test_invalid_run_configuration_fails_closed() -> None:
    with pytest.raises(ValueError, match="unique"):
        run_batch_evaluation(MANIFEST, seeds=(101, 101), cases_per_seed=30)

    with pytest.raises(ValueError, match="cases_per_seed"):
        run_batch_evaluation(MANIFEST, seeds=(101,), cases_per_seed=0)


def test_cli_writes_machine_readable_synthetic_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    exit_code = main(
        [
            "--manifest",
            str(MANIFEST),
            "--output",
            str(output),
            "--seed",
            "101",
            "--cases-per-seed",
            "30",
            "--confirm-synthetic",
        ]
    )

    assert exit_code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["source"] == "SYNTHETIC"
    assert document["simulation"]["seeds"] == [101]
    assert document["simulation"]["cases_per_seed"] == 30
    assert "not production" in document["simulation_disclosure"].lower()


def test_cli_requires_explicit_synthetic_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--manifest",
                str(MANIFEST),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )

    assert error.value.code == 2
