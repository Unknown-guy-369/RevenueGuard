"""Add observable dead-letter replay metadata.

Revision ID: 0003_phase2_dead_letter_replay
Revises: 0002_phase2_event_ingestion
Create Date: 2026-08-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_phase2_dead_letter_replay"
down_revision: str | Sequence[str] | None = "0002_phase2_event_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event_dispatches",
        sa.Column("replay_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "event_dispatches",
        sa.Column("last_replayed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "event_dispatches",
        sa.Column("last_replayed_by", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "ck_event_dispatches_replay_nonnegative",
        "event_dispatches",
        "replay_count >= 0",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Dead-letter replay metadata is historical workflow evidence and is not removed."
    )
