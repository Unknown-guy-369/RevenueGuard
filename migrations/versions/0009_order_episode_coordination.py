"""Coordinate payment-attempt recovery cases by merchant order episode.

Revision ID: 0009_order_episode_coordination
Revises: 0008_dashboard_simulations
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_order_episode_coordination"
down_revision: str | Sequence[str] | None = "0008_dashboard_simulations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Payment IDs are attempts. The episode key now uses the provider order when it
    # exists, so the database must enforce one case across those attempts.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_recovery_cases_episode"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_recovery_cases_episode "
            "ON recovery_cases (merchant_id, workflow_type, subject_type, "
            "recovery_episode_key) WHERE recovery_episode_key IS NOT NULL"
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "Recovery episode coordination protects durable financial workflow history "
        "and is not rolled back."
    )
