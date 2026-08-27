"""Persist bounded model predictions and link them to decision receipts.

Revision ID: 0006_phase5_agent_intelligence
Revises: 0005_phase4_exactly_once_effects
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase5_agent_intelligence"
down_revision: str | Sequence[str] | None = "0005_phase4_exactly_once_effects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_predictions",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "output_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=128), nullable=False),
        sa.Column("feature_version", sa.String(length=128), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "node IN ('DIAGNOSIS_ASSISTANCE', 'STRATEGY_GENERATION', 'RANKING', "
            "'EXPLANATION', 'GRAPH')",
            name="ck_model_predictions_node",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FALLBACK')",
            name="ck_model_predictions_status",
        ),
        sa.CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_model_predictions_input_digest",
        ),
        sa.CheckConstraint(
            "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ck_model_predictions_usage_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCEEDED' AND failure_code IS NULL) OR "
            "(status = 'FALLBACK' AND failure_code IS NOT NULL)",
            name="ck_model_predictions_failure_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id",
            "run_id",
            "node",
            name="uq_model_predictions_run_node",
        ),
    )
    op.create_index(
        "ix_model_predictions_case_created",
        "model_predictions",
        ["merchant_id", "recovery_case_id", "created_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER prevent_model_predictions_mutate "
            "AFTER UPDATE OR DELETE ON model_predictions "
            "FOR EACH ROW EXECUTE FUNCTION trg_immutable_insert_only();"
        )
    )
    op.add_column(
        "decision_receipts",
        sa.Column(
            "model_prediction_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 5 predictions and their decision-receipt links are historical decision evidence "
        "and are not removed."
    )
