"""PostgreSQL integration coverage for the forward-only audit ledger."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from revenueguard_integrations.persistence import (
    AuditAppend,
    AuditLedger,
    AuditVerificationStatus,
    Base,
    EventIngestionRepository,
    create_session_factory,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

DATABASE_URL = os.environ.get(
    "REVENUEGUARD_DATABASE_URL",
    "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard",
)
NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


@pytest.fixture
async def audit_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    administration_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        async with administration_engine.connect() as connection:
            await connection.execute(select(1))
    except (OSError, SQLAlchemyError) as exc:
        await administration_engine.dispose()
        pytest.skip(f"PostgreSQL integration dependency unavailable: {exc}")

    schema = f"audit_test_{uuid4().hex}"
    async with administration_engine.begin() as connection:
        await connection.execute(CreateSchema(schema))
    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(sync_connection, checkfirst=False)
            )
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
        async with administration_engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        await administration_engine.dispose()


async def test_ledger_starts_forward_only_and_verifies_its_canonical_chain(
    audit_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with audit_factory.begin() as session:
        await EventIngestionRepository(session).upsert_merchant(
            merchant_id="merchant_audit",
            display_name="Audit Merchant",
        )
        ledger = AuditLedger(session)
        entry = await ledger.append(
            AuditAppend(
                merchant_id="merchant_audit",
                event_type="ACTION_AUTHORIZED",
                aggregate_type="RECOVERY_ACTION",
                aggregate_id="action_001",
                correlation_id="correlation_001",
                actor_type="SYSTEM",
                actor_reference="recovery-service",
                payload={"action_type": "CREATE_PAYMENT_LINK", "amount_minor": 32400},
                recorded_at=NOW,
            )
        )

    assert entry.sequence == 2
    assert entry.previous_entry_hash != entry.entry_hash

    async with audit_factory() as session:
        verification = await AuditLedger(session).verify("merchant_audit")

    assert verification.status is AuditVerificationStatus.VALID
    assert verification.checked_entries == 2


async def test_ledger_reports_the_first_tampered_entry(
    audit_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with audit_factory.begin() as session:
        await EventIngestionRepository(session).upsert_merchant(
            merchant_id="merchant_tampered",
            display_name="Tampered Merchant",
        )
        ledger = AuditLedger(session)
        entry = await ledger.append(
            AuditAppend(
                merchant_id="merchant_tampered",
                event_type="ACTION_AUTHORIZED",
                aggregate_type="RECOVERY_ACTION",
                aggregate_id="action_001",
                correlation_id="correlation_001",
                actor_type="SYSTEM",
                actor_reference="recovery-service",
                payload={"action_type": "CREATE_PAYMENT_LINK"},
                recorded_at=NOW,
            )
        )
        entry.payload = {"action_type": "CREATE_PAYMENT_LINK", "tampered": True}

    async with audit_factory() as session:
        verification = await AuditLedger(session).verify("merchant_tampered")

    assert verification.status is AuditVerificationStatus.PAYLOAD_HASH_MISMATCH
    assert verification.first_broken_sequence == 2
