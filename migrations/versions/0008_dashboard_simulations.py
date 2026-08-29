"""Add durable synthetic checkout sessions for the merchant demo.

Revision ID: 0008_dashboard_simulations
Revises: 0007_phase6_core_playbooks
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_dashboard_simulations"
down_revision: str | Sequence[str] | None = "0007_phase6_core_playbooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "simulation_sessions",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=False),
        sa.Column("flow_type", sa.String(length=32), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("subscription_id", sa.String(length=128), nullable=True),
        sa.Column("provider_event_id", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scenario IN ('SUCCESS', 'INSUFFICIENT_FUNDS', 'ISSUER_OUTAGE', 'TIMEOUT')",
            name="ck_simulation_sessions_scenario",
        ),
        sa.CheckConstraint(
            "flow_type IN ('ONE_TIME', 'SUBSCRIPTION')",
            name="ck_simulation_sessions_flow",
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_simulation_sessions_amount"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_simulation_sessions_currency"
        ),
        sa.CheckConstraint(
            "status IN ('CREATED', 'SUBMITTED', 'EXPIRED')",
            name="ck_simulation_sessions_status",
        ),
        sa.CheckConstraint(
            "classification = 'SYNTHETIC'", name="ck_simulation_sessions_classification"
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_simulation_sessions_expiry"
        ),
        sa.CheckConstraint(
            "(status = 'CREATED' AND attempted_at IS NULL AND provider_event_id IS NULL) OR "
            "(status = 'SUBMITTED' AND attempted_at IS NOT NULL "
            "AND provider_event_id IS NOT NULL) OR "
            "(status = 'EXPIRED' AND provider_event_id IS NULL)",
            name="ck_simulation_sessions_lifecycle",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint("id", name="uq_simulation_sessions_public_id"),
        sa.UniqueConstraint(
            "merchant_id", "payment_id", name="uq_simulation_sessions_payment"
        ),
        sa.UniqueConstraint(
            "merchant_id", "provider_event_id", name="uq_simulation_sessions_event"
        ),
    )
    op.create_index(
        "ix_simulation_sessions_expiry",
        "simulation_sessions",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Synthetic simulation sessions are durable workflow evidence and are not removed."
    )
