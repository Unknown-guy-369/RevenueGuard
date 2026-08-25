"""Add the Phase 2 transactional event inbox and core provider identities.

Revision ID: 0002_phase2_event_ingestion
Revises: 0001_phase1
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_phase2_event_ingestion"
down_revision: str | Sequence[str] | None = "0001_phase1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_account_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_merchants_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_account_id", name="uq_merchants_provider_account"
        ),
    )

    op.create_table(
        "customers",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=128), nullable=True),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "provider_customer_id", name="uq_customers_merchant_provider_id"
        ),
    )

    op.create_table(
        "payments",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("order_id", sa.String(length=128), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("provider_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("amount_minor >= 0", name="ck_payments_amount_nonnegative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_payments_currency_iso"),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id", "provider_payment_id", name="uq_payments_merchant_provider_id"
        ),
    )

    op.create_table(
        "subscriptions",
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("provider_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("amount_minor >= 0", name="ck_subscriptions_amount_nonnegative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_subscriptions_currency_iso"),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
        sa.UniqueConstraint(
            "merchant_id",
            "provider_subscription_id",
            name="uq_subscriptions_merchant_provider_id",
        ),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=256), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=True),
        sa.Column("entity_id", sa.String(length=128), nullable=True),
        sa.Column("raw_body", sa.LargeBinary(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("signature_failure_code", sa.String(length=64), nullable=True),
        sa.Column("ingestion_status", sa.String(length=32), nullable=False),
        sa.Column("processing_state", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "ingestion_status IN ('ACCEPTED', 'REJECTED_INVALID_SIGNATURE')",
            name="ck_webhook_events_ingestion_status",
        ),
        sa.CheckConstraint(
            "processing_state IN ('NOT_QUEUED', 'PENDING', 'PROCESSING', 'PROCESSED', "
            "'RETRY_SCHEDULED', 'DEAD_LETTER')",
            name="ck_webhook_events_processing_state",
        ),
        sa.CheckConstraint(
            "NOT signature_valid OR (provider_event_id IS NOT NULL AND raw_body IS NOT NULL)",
            name="ck_webhook_events_valid_payload",
        ),
        sa.CheckConstraint(
            "signature_valid OR (raw_body IS NULL AND raw_payload IS NULL)",
            name="ck_webhook_events_rejected_payload_redacted",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "id", name="uq_webhook_events_merchant_id_id"),
    )
    op.create_index(
        "uq_webhook_events_verified_provider_event",
        "webhook_events",
        ["provider", "merchant_id", "provider_event_id"],
        unique=True,
        postgresql_where=sa.text("signature_valid"),
    )
    op.create_index(
        "ix_webhook_events_merchant_received",
        "webhook_events",
        ["merchant_id", "received_at"],
        unique=False,
    )

    op.create_table(
        "normalized_events",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("webhook_event_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_event_id", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("payment_id", sa.String(length=128), nullable=True),
        sa.Column("order_id", sa.String(length=128), nullable=True),
        sa.Column("subscription_id", sa.String(length=128), nullable=True),
        sa.Column("invoice_id", sa.String(length=128), nullable=True),
        sa.Column("payment_link_id", sa.String(length=128), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("normalized_failure_category", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("causation_id", sa.String(length=128), nullable=True),
        sa.Column("source_payload_reference", sa.String(length=512), nullable=False),
        sa.Column("normalized_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_minor >= 0", name="ck_normalized_events_amount_nonnegative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_normalized_events_currency_iso"),
        sa.CheckConstraint("schema_version = '1.0'", name="ck_normalized_events_schema_version"),
        sa.CheckConstraint(
            "source IN ('RAZORPAY', 'MERCHANT', 'SYNTHETIC')", name="ck_normalized_events_source"
        ),
        sa.CheckConstraint(
            "customer_id IS NOT NULL OR payment_id IS NOT NULL OR subscription_id IS NOT NULL "
            "OR invoice_id IS NOT NULL OR payment_link_id IS NOT NULL",
            name="ck_normalized_events_subject_reference",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["merchant_id", "payment_id"],
            ["payments.merchant_id", "payments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "subscription_id"],
            ["subscriptions.merchant_id", "subscriptions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "webhook_event_id"],
            ["webhook_events.merchant_id", "webhook_events.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "id", name="uq_normalized_events_merchant_id_id"),
        sa.UniqueConstraint(
            "merchant_id", "source", "source_event_id", name="uq_normalized_events_source_event"
        ),
        sa.UniqueConstraint("webhook_event_id", name="uq_normalized_events_webhook_event"),
    )
    op.create_index(
        "ix_normalized_events_merchant_occurred",
        "normalized_events",
        ["merchant_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_normalized_events_correlation",
        "normalized_events",
        ["merchant_id", "correlation_id"],
        unique=False,
    )

    op.create_table(
        "event_correlations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("normalized_event_id", sa.String(length=128), nullable=False),
        sa.Column("reference_type", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("internal_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reference_type IN ('CUSTOMER', 'PAYMENT', 'ORDER', 'SUBSCRIPTION', 'INVOICE', "
            "'PAYMENT_LINK')",
            name="ck_event_correlations_reference_type",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "normalized_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id",
            "normalized_event_id",
            "reference_type",
            "external_id",
            name="uq_event_correlations_reference",
        ),
    )
    op.create_index(
        "ix_event_correlations_external",
        "event_correlations",
        ["merchant_id", "reference_type", "external_id"],
        unique=False,
    )

    op.create_table(
        "event_dispatches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("webhook_event_id", sa.String(length=36), nullable=False),
        sa.Column("normalized_event_id", sa.String(length=128), nullable=True),
        sa.Column("queue_name", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker_task_id", sa.String(length=128), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_event_dispatches_attempt_nonnegative"),
        sa.CheckConstraint(
            "attempt_count <= max_attempts", name="ck_event_dispatches_attempt_within_limit"
        ),
        sa.CheckConstraint(
            "state <> 'DEAD_LETTER' OR dead_lettered_at IS NOT NULL",
            name="ck_event_dispatches_dead_letter_timestamp",
        ),
        sa.CheckConstraint("max_attempts > 0", name="ck_event_dispatches_max_attempts_positive"),
        sa.CheckConstraint(
            "state IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'RETRY_SCHEDULED', "
            "'SUCCEEDED', 'DEAD_LETTER')",
            name="ck_event_dispatches_state",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "normalized_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["merchant_id", "webhook_event_id"],
            ["webhook_events.merchant_id", "webhook_events.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("webhook_event_id", name="uq_event_dispatches_webhook_event"),
    )
    op.create_index(
        "ix_event_dispatches_claim",
        "event_dispatches",
        ["state", "available_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_event_dispatches_merchant_state",
        "event_dispatches",
        ["merchant_id", "state"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 2 contains accepted financial-event history and has no destructive downgrade. "
        "Restore a reviewed backup into a separate environment if rollback is required."
    )
