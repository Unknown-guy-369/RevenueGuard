#!/usr/bin/env python3
"""Configure a Test Mode merchant and its immutable conservative recovery policy."""

from __future__ import annotations

import argparse
import asyncio

from revenueguard_api.config import get_settings
from revenueguard_domain import conservative_default_policy
from revenueguard_integrations.persistence import (
    EventIngestionRepository,
    RecoveryRepository,
    create_database_engine,
    create_session_factory,
    session_scope,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merchant-id", required=True)
    parser.add_argument("--display-name", default="RevenueGuard Test Merchant")
    parser.add_argument("--provider-account-id")
    return parser.parse_args()


async def bootstrap(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            await EventIngestionRepository(session).upsert_merchant(
                merchant_id=args.merchant_id,
                display_name=args.display_name,
                provider_account_id=args.provider_account_id,
            )
            await RecoveryRepository(session).publish_policy(
                merchant_id=args.merchant_id,
                policy=conservative_default_policy(),
                published_by="BOOTSTRAP_SCRIPT",
            )
    finally:
        await engine.dispose()


def main() -> int:
    args = parse_args()
    asyncio.run(bootstrap(args))
    print(f"configured active Test Mode merchant: {args.merchant_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
