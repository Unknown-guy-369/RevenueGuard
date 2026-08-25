#!/usr/bin/env python3
"""Explicitly requeue one retained Phase 2 dead-letter dispatch."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from revenueguard_api.config import get_settings
from revenueguard_integrations.persistence import (
    EventIngestionRepository,
    create_database_engine,
    create_session_factory,
    session_scope,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dispatch_id")
    parser.add_argument("--actor", required=True, help="Operator or automation identity")
    return parser.parse_args()


async def requeue(dispatch_id: str, actor: str) -> bool:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        async with session_scope(session_factory) as session:
            return await EventIngestionRepository(session).requeue_dead_letter(
                dispatch_id=dispatch_id,
                replayed_at=datetime.now(UTC),
                replayed_by=actor,
            )
    finally:
        await engine.dispose()


def main() -> int:
    args = parse_args()
    if not asyncio.run(requeue(args.dispatch_id, args.actor)):
        print("dispatch not found or not in DEAD_LETTER state")
        return 1
    print(f"requeued dead-letter dispatch: {args.dispatch_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
