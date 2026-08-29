"""Complete the durable records for the three Phase 6 playbooks.

Revision ID: 0007_phase6_core_playbooks
Revises: 0006_phase5_agent_intelligence
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase6_core_playbooks"
down_revision: str | Sequence[str] | None = "0006_phase5_agent_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("provider_invoice_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("outstanding_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("automation_frozen_at", sa.DateTime(timezone=True), nullable=True),
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
            "amount_minor >= 0 AND outstanding_amount_minor >= 0 "
            "AND outstanding_amount_minor <= amount_minor",
            name="ck_invoices_amounts_valid",
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_invoices_currency_iso"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'OVERDUE', 'PROMISED', 'PAID', 'DISPUTED', "
            "'ESCALATED', 'CANCELLED')",
            name="ck_invoices_status",
        ),
        sa.CheckConstraint(
            "(status = 'DISPUTED' AND automation_frozen_at IS NOT NULL) OR "
            "(status <> 'DISPUTED')",
            name="ck_invoices_dispute_frozen",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "provider_invoice_id", name="uq_invoices_merchant_provider_id"
        ),
    )
    op.create_index(
        "ix_invoices_merchant_due", "invoices", ["merchant_id", "status", "due_at"]
    )

    op.create_table(
        "merchant_events",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("source_event_id", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('invoice.overdue', 'customer.response')",
            name="ck_merchant_events_type",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_merchant_events_payload_digest"
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "source_event_id", name="uq_merchant_events_source_event"
        ),
    )
    op.create_index(
        "ix_merchant_events_received", "merchant_events", ["merchant_id", "received_at"]
    )

    op.alter_column("normalized_events", "webhook_event_id", nullable=True)
    op.add_column(
        "normalized_events", sa.Column("merchant_event_id", sa.String(length=128), nullable=True)
    )
    op.create_foreign_key(
        "fk_normalized_events_merchant_event",
        "normalized_events",
        "merchant_events",
        ["merchant_id", "merchant_event_id"],
        ["merchant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_normalized_events_merchant_event", "normalized_events", ["merchant_event_id"]
    )
    op.create_check_constraint(
        "ck_normalized_events_one_inbox_source",
        "normalized_events",
        "(webhook_event_id IS NOT NULL)::integer + "
        "(merchant_event_id IS NOT NULL)::integer = 1",
    )

    _extend_incidents()
    _create_customer_response_tables()
    _create_degradation_tables()


def _extend_incidents() -> None:
    op.add_column(
        "portfolio_incidents",
        sa.Column("dimension_key", sa.String(length=384), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE portfolio_incidents SET dimension_key = 'LEGACY|' || id "
            "WHERE dimension_key IS NULL"
        )
    )
    op.alter_column(
        "portfolio_incidents",
        "dimension_key",
        nullable=False,
        server_default="LEGACY",
    )
    columns = (
        sa.Column("payment_method", sa.String(length=128), nullable=True),
        sa.Column("issuer_family", sa.String(length=128), nullable=True),
        sa.Column("error_family", sa.String(length=128), nullable=True),
        sa.Column("baseline_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("baseline_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "baseline_failure_rate_basis_points",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "current_failure_rate_basis_points",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "threshold_version",
            sa.String(length=128),
            nullable=False,
            server_default="phase6-playbooks-1.0",
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("clear_window_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reason", sa.String(length=128), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    for column in columns:
        op.add_column("portfolio_incidents", column)
    op.create_check_constraint(
        "ck_portfolio_incidents_counts",
        "portfolio_incidents",
        "baseline_total >= 0 AND baseline_failures >= 0 "
        "AND baseline_failures <= baseline_total "
        "AND current_total >= 0 AND current_failures >= 0 "
        "AND current_failures <= current_total",
    )
    op.create_check_constraint(
        "ck_portfolio_incidents_rates",
        "portfolio_incidents",
        "baseline_failure_rate_basis_points BETWEEN 0 AND 10000 "
        "AND current_failure_rate_basis_points BETWEEN 0 AND 10000",
    )
    op.create_check_constraint(
        "ck_portfolio_incidents_clear_windows",
        "portfolio_incidents",
        "clear_window_count >= 0",
    )
    op.create_check_constraint(
        "ck_portfolio_incidents_resolution",
        "portfolio_incidents",
        "(status = 'ACTIVE' AND resolved_at IS NULL AND resolution_reason IS NULL) OR "
        "(status = 'RESOLVED' AND resolved_at IS NOT NULL AND resolution_reason IS NOT NULL)",
    )
    op.create_index(
        "uq_portfolio_incidents_active_dimension",
        "portfolio_incidents",
        ["merchant_id", "dimension_key"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def _create_customer_response_tables() -> None:
    op.create_table(
        "customer_responses",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("source_response_id", sa.String(length=256), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("promised_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("extractor_version", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("body_sha256 ~ '^[0-9a-f]{64}$'", name="ck_responses_body_digest"),
        sa.CheckConstraint(
            "intent IN ('PROMISE_TO_PAY', 'DISPUTE', 'ALREADY_PAID', 'NEEDS_HELP', 'UNKNOWN')",
            name="ck_responses_intent",
        ),
        sa.CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000", name="ck_responses_confidence"
        ),
        sa.CheckConstraint(
            "(intent = 'PROMISE_TO_PAY' AND promised_for IS NOT NULL "
            "AND amount_minor > 0 AND currency ~ '^[A-Z]{3}$') OR "
            "(intent <> 'PROMISE_TO_PAY' AND promised_for IS NULL "
            "AND amount_minor IS NULL AND currency IS NULL)",
            name="ck_responses_promise_terms",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "invoice_id"],
            ["invoices.merchant_id", "invoices.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "source_response_id", name="uq_customer_responses_source"
        ),
    )
    op.create_table(
        "promises_to_pay",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("customer_response_id", sa.String(length=128), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("promised_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("extractor_version", sa.String(length=128), nullable=False),
        sa.Column("extraction_confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_action_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_promises_amount_positive"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_promises_currency_iso"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'FULFILLED', 'BROKEN', 'DISPUTED', 'CANCELLED')",
            name="ck_promises_status",
        ),
        sa.CheckConstraint(
            "extraction_confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_promises_confidence",
        ),
        sa.CheckConstraint(
            "(status = 'FULFILLED' AND fulfilled_at IS NOT NULL) OR status <> 'FULFILLED'",
            name="ck_promises_fulfilled_at",
        ),
        sa.CheckConstraint(
            "(status = 'BROKEN' AND broken_at IS NOT NULL) OR status <> 'BROKEN'",
            name="ck_promises_broken_at",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_response_id"],
            ["customer_responses.merchant_id", "customer_responses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "invoice_id"],
            ["invoices.merchant_id", "invoices.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "reminder_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint("merchant_id", "customer_response_id", name="uq_promises_response"),
    )
    op.create_index(
        "ix_promises_due_reminder",
        "promises_to_pay",
        ["status", "reminder_at"],
        postgresql_where=sa.text("status = 'ACTIVE' AND reminder_action_id IS NULL"),
    )
    op.create_index(
        "ix_promises_due_break",
        "promises_to_pay",
        ["status", "promised_for"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "receivable_escalations",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("invoice_id", sa.String(length=128), nullable=False),
        sa.Column("customer_response_id", sa.String(length=128), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_escalations_status"),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_response_id"],
            ["customer_responses.merchant_id", "customer_responses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "invoice_id"],
            ["invoices.merchant_id", "invoices.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "customer_response_id", name="uq_receivable_escalations_response"
        ),
    )


def _create_degradation_tables() -> None:
    op.create_table(
        "payment_outcome_observations",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("payment_id", sa.String(length=128), nullable=False),
        sa.Column("source_event_id", sa.String(length=256), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("payment_method", sa.String(length=128), nullable=False),
        sa.Column("issuer_family", sa.String(length=128), nullable=False),
        sa.Column("error_family", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "source_event_id", name="uq_payment_observations_source_event"
        ),
    )
    op.create_index(
        "ix_payment_observations_dimension_time",
        "payment_outcome_observations",
        [
            "merchant_id",
            "payment_method",
            "issuer_family",
            "error_family",
            "occurred_at",
        ],
    )
    op.create_table(
        "incident_case_links",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("incident_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resume_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resume_after IS NULL OR resume_after >= attached_at",
            name="ck_incident_case_links_resume_order",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "incident_id"],
            ["portfolio_incidents.merchant_id", "portfolio_incidents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "incident_id", "recovery_case_id"),
    )
    op.create_index(
        "ix_incident_case_links_resume",
        "incident_case_links",
        ["merchant_id", "resume_after", "resumed_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 6 receivable, promise, incident, and outcome records are financial workflow "
        "history and are not removed."
    )
