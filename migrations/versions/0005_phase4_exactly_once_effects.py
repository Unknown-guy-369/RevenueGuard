"""Add the action outbox, execution attempts, and verified outcomes.

Revision ID: 0005_phase4_exactly_once_effects
Revises: 0004_phase3_recovery_policy
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase4_exactly_once_effects"
down_revision: str | Sequence[str] | None = "0004_phase3_recovery_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recovery_actions",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("decision_receipt_id", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("logical_attempt", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execute_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_object_id", sa.String(length=128), nullable=True),
        sa.Column("unknown_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version = '1.0'", name="ck_recovery_actions_schema_version"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_recovery_actions_status",
        ),
        sa.CheckConstraint("logical_attempt >= 1", name="ck_recovery_actions_logical_attempt"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_recovery_actions_attempt_bounds",
        ),
        sa.CheckConstraint(
            "execute_after >= authorized_at AND next_attempt_at >= authorized_at",
            name="ck_recovery_actions_schedule",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_recovery_actions_lease_pair",
        ),
        sa.CheckConstraint(
            "(status = 'UNKNOWN' AND unknown_since IS NOT NULL AND "
            "reconciliation_deadline IS NOT NULL) OR "
            "(status <> 'UNKNOWN' AND unknown_since IS NULL)",
            name="ck_recovery_actions_unknown_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "decision_receipt_id"],
            ["decision_receipts.merchant_id", "decision_receipts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint("idempotency_key", name="uq_recovery_actions_idempotency_key"),
    )
    op.create_index(
        "uq_recovery_actions_active_equivalent",
        "recovery_actions",
        ["merchant_id", "recovery_case_id", "action_type", "target_type", "target_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'SUCCEEDED', 'UNKNOWN')"),
    )
    op.create_index(
        "ix_recovery_actions_due",
        "recovery_actions",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status = 'PENDING' AND dead_lettered_at IS NULL"),
    )
    op.create_index(
        "ix_recovery_actions_unknown",
        "recovery_actions",
        ["status", "reconciliation_deadline"],
        postgresql_where=sa.text("status = 'UNKNOWN'"),
    )

    op.create_table(
        "action_attempts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_action_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_status", sa.String(length=32), nullable=True),
        sa.Column("response_category", sa.String(length=64), nullable=True),
        sa.Column("provider_object_id", sa.String(length=128), nullable=True),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("response_reference", sa.String(length=512), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.CheckConstraint("attempt_number >= 1", name="ck_action_attempts_number"),
        sa.CheckConstraint(
            "outcome_status IS NULL OR outcome_status IN "
            "('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_action_attempts_outcome_status",
        ),
        sa.CheckConstraint(
            "(completed_at IS NULL AND outcome_status IS NULL) OR "
            "(completed_at IS NOT NULL AND outcome_status IS NOT NULL AND "
            "completed_at >= started_at)",
            name="ck_action_attempts_completion",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id",
            "recovery_action_id",
            "attempt_number",
            name="uq_action_attempts_action_number",
        ),
        sa.UniqueConstraint("request_id", name="uq_action_attempts_request_id"),
    )
    op.create_index(
        "ix_action_attempts_action",
        "action_attempts",
        ["merchant_id", "recovery_action_id", "attempt_number"],
    )

    op.create_table(
        "verified_outcomes",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("recovery_action_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("outcome_status", sa.String(length=32), nullable=False),
        sa.Column("is_authoritative", sa.Boolean(), nullable=False),
        sa.Column("evidence_source", sa.String(length=32), nullable=False),
        sa.Column("evidence_reference", sa.String(length=512), nullable=True),
        sa.Column("provider_object_id", sa.String(length=128), nullable=True),
        sa.Column("recovered_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.CheckConstraint("schema_version = '1.0'", name="ck_verified_outcomes_schema_version"),
        sa.CheckConstraint(
            "outcome_status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_verified_outcomes_status",
        ),
        sa.CheckConstraint(
            "evidence_source IN ('SIGNED_WEBHOOK', 'PROVIDER_LOOKUP', 'PROVIDER_RESPONSE', "
            "'SIMULATOR', 'NONE')",
            name="ck_verified_outcomes_evidence_source",
        ),
        sa.CheckConstraint(
            "recovered_amount_minor >= 0", name="ck_verified_outcomes_amount_nonnegative"
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_verified_outcomes_currency_iso"),
        sa.CheckConstraint(
            "recovered_amount_minor = 0 OR (outcome_status = 'SUCCEEDED' AND "
            "is_authoritative AND evidence_reference IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_verified_outcomes_recovered_authority",
        ),
        sa.CheckConstraint(
            "outcome_status <> 'UNKNOWN' OR (NOT is_authoritative AND "
            "recovered_amount_minor = 0 AND verified_at IS NULL)",
            name="ck_verified_outcomes_unknown",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
    )
    op.create_index(
        "uq_verified_outcomes_authoritative_success",
        "verified_outcomes",
        ["merchant_id", "recovery_action_id"],
        unique=True,
        postgresql_where=sa.text("is_authoritative AND outcome_status = 'SUCCEEDED'"),
    )
    op.create_index(
        "ix_verified_outcomes_metrics",
        "verified_outcomes",
        ["merchant_id", "currency", "verified_at"],
        postgresql_where=sa.text(
            "is_authoritative AND outcome_status = 'SUCCEEDED' AND recovered_amount_minor > 0"
        ),
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER prevent_verified_outcomes_mutate "
            "AFTER UPDATE OR DELETE ON verified_outcomes "
            "FOR EACH ROW EXECUTE FUNCTION trg_immutable_insert_only();"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 4 action attempts and verified financial outcomes are historical workflow evidence "
        "and are not removed."
    )
