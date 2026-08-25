#!/usr/bin/env python3
"""Replay sanitized Razorpay webhook fixtures against RevenueGuard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from revenueguard_evaluation.webhook_replay import (
    ReplayMode,
    load_fixtures,
    make_http_sender,
    plan_replay,
    run_replay,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=[mode.value for mode in ReplayMode])
    parser.add_argument("fixtures", nargs="+", type=Path)
    parser.add_argument(
        "--endpoint",
        default="http://localhost:8000/api/v1/webhooks/razorpay",
    )
    parser.add_argument("--merchant-id", required=True)
    parser.add_argument("--duplicate-count", type=int, default=5)
    parser.add_argument("--burst-size", type=int, default=25)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not webhook_secret:
        raise SystemExit("RAZORPAY_WEBHOOK_SECRET must be set; secrets are not accepted on the CLI")

    mode = ReplayMode(args.mode)
    fixtures = load_fixtures(args.fixtures)
    plan = plan_replay(
        fixtures,
        mode,
        duplicate_count=args.duplicate_count,
        burst_size=args.burst_size,
    )
    sender = make_http_sender(args.endpoint, args.merchant_id, webhook_secret)
    summary = run_replay(
        plan,
        mode,
        sender,
        delay_seconds=args.delay_seconds,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary.as_dict(), sort_keys=True))

    expected_rejection = mode is ReplayMode.INVALID_SIGNATURE
    if summary.failures or (expected_rejection and summary.rejected != summary.received):
        return 1
    if not expected_rejection and summary.accepted + summary.duplicates != summary.received:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
