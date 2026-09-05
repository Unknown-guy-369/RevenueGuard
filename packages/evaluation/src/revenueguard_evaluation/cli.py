"""Manual command-line interface for the synthetic batch evaluator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from revenueguard_evaluation.batch import run_batch_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RevenueGuard synthetic evaluator")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, action="append", dest="seeds")
    parser.add_argument("--cases-per-seed", type=int, default=240)
    parser.add_argument("--confirm-synthetic", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.confirm_synthetic:
        parser.error("--confirm-synthetic is required")
    report = run_batch_evaluation(
        arguments.manifest,
        seeds=tuple(arguments.seeds or (101, 202, 303, 404, 505, 606, 707, 808, 909, 1010)),
        cases_per_seed=arguments.cases_per_seed,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the public main seam
    raise SystemExit(main())
