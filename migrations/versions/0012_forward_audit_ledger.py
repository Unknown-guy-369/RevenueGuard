"""Create forward-only, merchant-scoped audit ledger tables.

Revision ID: 0012_forward_audit_ledger
Revises: 0011_integrated_sim_recovery
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_forward_audit_ledger"
down_revision: str | Sequence[str] | None = "0011_integrated_sim_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_ledger_heads",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("latest_sequence", sa.Integer(), nullable=False),
        sa.Column("latest_entry_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("latest_sequence >= 0", name="ck_audit_ledger_heads_sequence"),
        sa.CheckConstraint(
            "latest_entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_ledger_heads_hash"
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id"),
    )
    op.create_table(
        "audit_entries",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("causation_id", sa.String(length=128), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_reference", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("previous_entry_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=True),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.String(length=128), nullable=True),
        sa.Column("feature_version", sa.String(length=128), nullable=True),
        sa.Column("application_version", sa.String(length=128), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_audit_entries_sequence"),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_audit_entries_payload_hash"
        ),
        sa.CheckConstraint(
            "previous_entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_entries_previous_hash"
        ),
        sa.CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_entries_hash"),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "sequence"),
        sa.UniqueConstraint("entry_id"),
        sa.UniqueConstraint("merchant_id", "entry_hash", name="uq_audit_entries_merchant_hash"),
    )
    op.create_index(
        "ix_audit_entries_aggregate",
        "audit_entries",
        ["merchant_id", "aggregate_type", "aggregate_id"],
    )
    op.create_index(
        "ix_audit_entries_correlation",
        "audit_entries",
        ["merchant_id", "correlation_id", "sequence"],
    )


def downgrade() -> None:
    raise RuntimeError("Forward-only audit history is not deleted during application rollback.")
