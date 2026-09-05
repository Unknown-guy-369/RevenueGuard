"""Allow an authentication-failure scenario for integrated recovery demonstrations.

Revision ID: 0011_integrated_sim_recovery
Revises: 0010_phase7_portfolio
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_integrated_sim_recovery"
down_revision: str | Sequence[str] | None = "0010_phase7_portfolio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_simulation_sessions_scenario",
        "simulation_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_simulation_sessions_scenario",
        "simulation_sessions",
        "scenario IN ('SUCCESS', 'INSUFFICIENT_FUNDS', 'AUTHENTICATION_FAILURE', "
        "'ISSUER_OUTAGE', 'TIMEOUT')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Synthetic simulation sessions are durable workflow evidence and are not rewritten."
    )
