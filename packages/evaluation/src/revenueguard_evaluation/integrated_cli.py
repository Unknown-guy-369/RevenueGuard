"""Command-line entry point for a live local integrated synthetic batch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from revenueguard_evaluation.batch import load_held_out_manifest
from revenueguard_evaluation.integrated import HttpIntegratedApi, run_integrated_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a synthetic batch through a live RevenueGuard API/worker stack"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--merchant-id", default=os.getenv("REVENUEGUARD_DASHBOARD_MERCHANT_ID"))
    parser.add_argument("--dashboard-token", default=os.getenv("REVENUEGUARD_DASHBOARD_API_TOKEN"))
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--poll-interval-seconds", type=float, default=1)
    parser.add_argument("--confirm-synthetic", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.confirm_synthetic:
        parser.error("--confirm-synthetic is required")
    if not arguments.merchant_id:
        parser.error("--merchant-id or REVENUEGUARD_DASHBOARD_MERCHANT_ID is required")
    if not arguments.dashboard_token:
        parser.error("--dashboard-token or REVENUEGUARD_DASHBOARD_API_TOKEN is required")

    report = run_integrated_batch(
        HttpIntegratedApi(
            base_url=arguments.api_url,
            merchant_id=arguments.merchant_id,
            dashboard_token=arguments.dashboard_token,
        ),
        manifest=load_held_out_manifest(arguments.manifest),
        timeout_seconds=arguments.timeout_seconds,
        poll_interval_seconds=arguments.poll_interval_seconds,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["batch"], indent=2, sort_keys=True))
    print(f"result={report['result']} report={arguments.output}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
