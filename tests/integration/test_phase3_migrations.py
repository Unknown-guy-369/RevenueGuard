"""Isolated clean and incremental Alembic validation through the current phase."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from revenueguard_domain import conservative_default_policy
from revenueguard_integrations.persistence import RecoveryRepository, create_session_factory
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["REVENUEGUARD_ALEMBIC_DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(arguments)} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


async def _create_database(name: str) -> str:
    base = make_url(DATABASE_URL)
    admin_url = base.set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()
    return base.set(database=name).render_as_string(hide_password=False)


async def _drop_database(name: str) -> None:
    base = make_url(DATABASE_URL)
    engine = create_async_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": name},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        await engine.dispose()


async def test_clean_and_0003_to_head_migrations_are_isolated_and_current() -> None:
    probe = create_async_engine(DATABASE_URL)
    try:
        async with probe.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await probe.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")
    await probe.dispose()

    clean_name = f"revenueguard_clean_{uuid4().hex}"
    incremental_name = f"revenueguard_incremental_{uuid4().hex}"
    clean_url: str | None = None
    incremental_url: str | None = None
    try:
        clean_url = await _create_database(clean_name)
        _run_alembic(clean_url, "upgrade", "head")
        current = _run_alembic(clean_url, "current")
        assert "0010_phase7_portfolio (head)" in current.stdout
        _run_alembic(clean_url, "check")

        incremental_url = await _create_database(incremental_name)
        _run_alembic(incremental_url, "upgrade", "0003_phase2_dead_letter_replay")
        incremental_engine = create_async_engine(incremental_url)
        async with incremental_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO merchants "
                    "(id, display_name, provider, provider_account_id, status) "
                    "VALUES ('merchant_migration', 'Migration Merchant', 'RAZORPAY', "
                    "'account_migration', 'ACTIVE')"
                )
            )
        await incremental_engine.dispose()

        _run_alembic(incremental_url, "upgrade", "head")
        _run_alembic(incremental_url, "current")
        _run_alembic(incremental_url, "check")

        engine = create_async_engine(incremental_url)
        factory = create_session_factory(engine)
        try:
            async with factory() as session:
                policy = await RecoveryRepository(session).effective_policy(
                    merchant_id="merchant_migration",
                    evaluated_at=conservative_default_policy().effective_at,
                )
                assert policy.content_digest == conservative_default_policy().content_digest
                trigger_names = set(
                    (
                        await session.scalars(
                            text(
                                "SELECT tgname FROM pg_trigger "
                                "WHERE NOT tgisinternal AND tgname IN "
                                "('prevent_merchant_policy_versions_mutate', "
                                "'prevent_case_transitions_mutate', "
                                "'prevent_decision_receipts_mutate', "
                                "'prevent_model_predictions_mutate')"
                            )
                        )
                    ).all()
                )
                assert trigger_names == {
                    "prevent_merchant_policy_versions_mutate",
                    "prevent_case_transitions_mutate",
                    "prevent_decision_receipts_mutate",
                    "prevent_model_predictions_mutate",
                }
                with pytest.raises(DBAPIError):
                    await session.execute(
                        text(
                            "UPDATE merchant_policy_versions SET published_by = 'MUTATED' "
                            "WHERE merchant_id = 'merchant_migration'"
                        )
                    )
                await session.rollback()
        finally:
            await engine.dispose()
    finally:
        if clean_url is not None:
            await _drop_database(clean_name)
        if incremental_url is not None:
            await _drop_database(incremental_name)
