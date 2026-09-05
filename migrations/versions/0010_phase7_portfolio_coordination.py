"""Add Phase 7 scoring evidence and durable customer coordination.

Revision ID: 0010_phase7_portfolio
Revises: 0009_order_episode_coordination
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_phase7_portfolio"
down_revision: str | Sequence[str] | None = "0009_order_episode_coordination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_case_transitions_allowed_edge",
        "case_transitions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_case_transitions_allowed_edge",
        "case_transitions",
        "(before_state = 'DETECTED' AND after_state IN ('DIAGNOSING')) OR "
        "(before_state = 'DIAGNOSING' AND after_state IN ('DECISION_PENDING')) OR "
        "(before_state = 'DECISION_PENDING' AND after_state IN ('POLICY_CHECK')) OR "
        "(before_state = 'POLICY_CHECK' AND after_state IN "
        "('READY', 'DEFERRED', 'DECISION_PENDING', 'ESCALATED', 'STOPPED')) OR "
        "(before_state = 'READY' AND after_state IN ('EXECUTING')) OR "
        "(before_state = 'EXECUTING' AND after_state IN ('VERIFYING', 'UNKNOWN')) OR "
        "(before_state = 'VERIFYING' AND after_state IN "
        "('RECOVERED', 'DECISION_PENDING', 'STOPPED', 'UNKNOWN')) OR "
        "(before_state = 'UNKNOWN' AND after_state IN ('VERIFYING', 'ESCALATED')) OR "
        "(before_state = 'DEFERRED' AND after_state IN ('DECISION_PENDING')) OR "
        "(before_state = 'ESCALATED' AND after_state IN ('DECISION_PENDING', 'STOPPED'))",
    )
    for name in (
        "scoring_model_version",
        "scoring_feature_version",
        "scoring_economics_version",
    ):
        op.add_column(
            "decision_receipts",
            sa.Column(
                name,
                sa.String(length=128),
                nullable=False,
                server_default="NOT_APPLICABLE",
            ),
        )
    op.add_column(
        "decision_receipts",
        sa.Column(
            "scoring_artifact_classification",
            sa.String(length=32),
            nullable=False,
            server_default="NOT_APPLICABLE",
        ),
    )
    op.add_column(
        "decision_receipts",
        sa.Column("scoring_fallback_reason", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "ck_decision_receipts_scoring_classification",
        "decision_receipts",
        "scoring_artifact_classification IN ('SYNTHETIC', 'PRODUCTION', 'NOT_APPLICABLE')",
    )

    op.create_table(
        "customer_interventions",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("owner_case_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_action_id", sa.String(length=128), nullable=False),
        sa.Column(
            "coordinated_case_ids",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=128), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'CLOSED')",
            name="ck_customer_interventions_status",
        ),
        sa.CheckConstraint(
            "cooldown_until > created_at",
            name="ck_customer_interventions_cooldown",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(coordinated_case_ids) = 'array' "
            "AND jsonb_array_length(coordinated_case_ids) > 0",
            name="ck_customer_interventions_case_ids",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND closed_at IS NULL AND close_reason IS NULL) OR "
            "(status = 'CLOSED' AND closed_at IS NOT NULL AND close_reason IS NOT NULL)",
            name="ck_customer_interventions_close_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "owner_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "recovery_action_id", name="uq_customer_interventions_action"
        ),
    )
    op.create_index(
        "uq_customer_interventions_active_customer",
        "customer_interventions",
        ["merchant_id", "customer_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_customer_interventions_maintenance",
        "customer_interventions",
        ["status", "cooldown_until"],
        unique=False,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_recovery_cases_customer_contact",
        "recovery_cases",
        ["merchant_id", "customer_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 7 customer coordination and scoring evidence are financial workflow history "
        "and are not rolled back."
    )
