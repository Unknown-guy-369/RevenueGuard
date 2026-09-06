"""SQLAlchemy models for durable ingestion and recovery decisioning.

All provider and workflow records are merchant scoped. PostgreSQL is authoritative;
the models deliberately retain provider time separately from system receive/process time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_DOCUMENT = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class Base(DeclarativeBase):
    """Declarative base shared by the API, worker, and migrations."""


class TimestampColumns:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


class Merchant(TimestampColumns, Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="RAZORPAY")
    provider_account_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")

    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id", name="uq_merchants_provider_account"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="ck_merchants_status"),
    )


class AuditLedgerHead(Base):
    """The locked, merchant-scoped head of a forward-only audit chain."""

    __tablename__ = "audit_ledger_heads"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    latest_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        CheckConstraint("latest_sequence >= 0", name="ck_audit_ledger_heads_sequence"),
        CheckConstraint("latest_entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_ledger_heads_hash"),
    )


class AuditEntry(Base):
    """One immutable, redacted entry in a merchant's tamper-evident history."""

    __tablename__ = "audit_entries"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(128))
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str | None] = mapped_column(String(128))
    feature_version: Mapped[str | None] = mapped_column(String(128))
    application_version: Mapped[str | None] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint("merchant_id", "entry_hash", name="uq_audit_entries_merchant_hash"),
        CheckConstraint("sequence >= 1", name="ck_audit_entries_sequence"),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_audit_entries_payload_hash"),
        CheckConstraint(
            "previous_entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_entries_previous_hash"
        ),
        CheckConstraint("entry_hash ~ '^[0-9a-f]{64}$'", name="ck_audit_entries_hash"),
        Index("ix_audit_entries_aggregate", "merchant_id", "aggregate_type", "aggregate_id"),
        Index("ix_audit_entries_correlation", "merchant_id", "correlation_id", "sequence"),
    )


class Customer(TimestampColumns, Base):
    __tablename__ = "customers"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(128))
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "merchant_id", "provider_customer_id", name="uq_customers_merchant_provider_id"
        ),
    )


class Payment(TimestampColumns, Base):
    __tablename__ = "payments"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(128))
    order_id: Mapped[str | None] = mapped_column(String(128))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id", "provider_payment_id", name="uq_payments_merchant_provider_id"
        ),
        CheckConstraint("amount_minor >= 0", name="ck_payments_amount_nonnegative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_payments_currency_iso"),
    )


class Subscription(TimestampColumns, Base):
    __tablename__ = "subscriptions"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_subscription_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(128))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id",
            "provider_subscription_id",
            name="uq_subscriptions_merchant_provider_id",
        ),
        CheckConstraint("amount_minor >= 0", name="ck_subscriptions_amount_nonnegative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_subscriptions_currency_iso"),
    )


class Invoice(TimestampColumns, Base):
    __tablename__ = "invoices"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outstanding_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    automation_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id", "provider_invoice_id", name="uq_invoices_merchant_provider_id"
        ),
        CheckConstraint(
            "amount_minor >= 0 AND outstanding_amount_minor >= 0 "
            "AND outstanding_amount_minor <= amount_minor",
            name="ck_invoices_amounts_valid",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_invoices_currency_iso"),
        CheckConstraint(
            "status IN ('OPEN', 'OVERDUE', 'PROMISED', 'PAID', 'DISPUTED', "
            "'ESCALATED', 'CANCELLED')",
            name="ck_invoices_status",
        ),
        CheckConstraint(
            "(status = 'DISPUTED' AND automation_frozen_at IS NOT NULL) OR (status <> 'DISPUTED')",
            name="ck_invoices_dispute_frozen",
        ),
        Index("ix_invoices_merchant_due", "merchant_id", "status", "due_at"),
    )


class MerchantEvent(Base):
    """Authenticated non-provider event inbox for receivables and replies."""

    __tablename__ = "merchant_events"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint("merchant_id", "source_event_id", name="uq_merchant_events_source_event"),
        CheckConstraint(
            "event_type IN ('invoice.overdue', 'customer.response')",
            name="ck_merchant_events_type",
        ),
        CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_merchant_events_payload_digest"
        ),
        Index("ix_merchant_events_received", "merchant_id", "received_at"),
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(256))
    event_type: Mapped[str | None] = mapped_column(String(128))
    entity_id: Mapped[str | None] = mapped_column(String(128))
    raw_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    signature_failure_code: Mapped[str | None] = mapped_column(String(64))
    ingestion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_state: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint("merchant_id", "id", name="uq_webhook_events_merchant_id_id"),
        CheckConstraint(
            "ingestion_status IN ('ACCEPTED', 'REJECTED_INVALID_SIGNATURE')",
            name="ck_webhook_events_ingestion_status",
        ),
        CheckConstraint(
            "processing_state IN ('NOT_QUEUED', 'PENDING', 'PROCESSING', 'PROCESSED', "
            "'RETRY_SCHEDULED', 'DEAD_LETTER')",
            name="ck_webhook_events_processing_state",
        ),
        CheckConstraint(
            "NOT signature_valid OR (provider_event_id IS NOT NULL AND raw_body IS NOT NULL)",
            name="ck_webhook_events_valid_payload",
        ),
        CheckConstraint(
            "signature_valid OR (raw_body IS NULL AND raw_payload IS NULL)",
            name="ck_webhook_events_rejected_payload_redacted",
        ),
        Index(
            "uq_webhook_events_verified_provider_event",
            "provider",
            "merchant_id",
            "provider_event_id",
            unique=True,
            postgresql_where=text("signature_valid"),
        ),
        Index("ix_webhook_events_merchant_received", "merchant_id", "received_at"),
    )


class NormalizedEvent(Base):
    __tablename__ = "normalized_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    webhook_event_id: Mapped[str | None] = mapped_column(String(36))
    merchant_event_id: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(128))
    payment_id: Mapped[str | None] = mapped_column(String(128))
    order_id: Mapped[str | None] = mapped_column(String(128))
    subscription_id: Mapped[str | None] = mapped_column(String(128))
    invoice_id: Mapped[str | None] = mapped_column(String(128))
    payment_link_id: Mapped[str | None] = mapped_column(String(128))
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    normalized_failure_category: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(128))
    source_payload_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "webhook_event_id"],
            ["webhook_events.merchant_id", "webhook_events.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "merchant_event_id"],
            ["merchant_events.merchant_id", "merchant_events.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "payment_id"],
            ["payments.merchant_id", "payments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "subscription_id"],
            ["subscriptions.merchant_id", "subscriptions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("merchant_id", "id", name="uq_normalized_events_merchant_id_id"),
        UniqueConstraint("webhook_event_id", name="uq_normalized_events_webhook_event"),
        UniqueConstraint("merchant_event_id", name="uq_normalized_events_merchant_event"),
        UniqueConstraint(
            "merchant_id", "source", "source_event_id", name="uq_normalized_events_source_event"
        ),
        CheckConstraint("schema_version = '1.0'", name="ck_normalized_events_schema_version"),
        CheckConstraint(
            "source IN ('RAZORPAY', 'MERCHANT', 'SYNTHETIC')",
            name="ck_normalized_events_source",
        ),
        CheckConstraint("amount_minor >= 0", name="ck_normalized_events_amount_nonnegative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_normalized_events_currency_iso"),
        CheckConstraint(
            "(webhook_event_id IS NOT NULL)::integer + "
            "(merchant_event_id IS NOT NULL)::integer = 1",
            name="ck_normalized_events_one_inbox_source",
        ),
        CheckConstraint(
            "customer_id IS NOT NULL OR payment_id IS NOT NULL OR subscription_id IS NOT NULL "
            "OR invoice_id IS NOT NULL OR payment_link_id IS NOT NULL",
            name="ck_normalized_events_subject_reference",
        ),
        Index("ix_normalized_events_merchant_occurred", "merchant_id", "occurred_at"),
        Index("ix_normalized_events_correlation", "merchant_id", "correlation_id"),
    )


class EventCorrelation(Base):
    __tablename__ = "event_correlations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    internal_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "normalized_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "merchant_id",
            "normalized_event_id",
            "reference_type",
            "external_id",
            name="uq_event_correlations_reference",
        ),
        CheckConstraint(
            "reference_type IN ('CUSTOMER', 'PAYMENT', 'ORDER', 'SUBSCRIPTION', 'INVOICE', "
            "'PAYMENT_LINK')",
            name="ck_event_correlations_reference_type",
        ),
        Index(
            "ix_event_correlations_external",
            "merchant_id",
            "reference_type",
            "external_id",
        ),
    )


class MerchantPolicyVersion(Base):
    __tablename__ = "merchant_policy_versions"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[str] = mapped_column(String(128), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "merchant_id", "effective_at", name="uq_merchant_policy_versions_effective_at"
        ),
        UniqueConstraint(
            "merchant_id", "version", "content_sha256", name="uq_policy_versions_identity_digest"
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_policy_versions_content_sha256"
        ),
        Index(
            "ix_merchant_policy_versions_effective",
            "merchant_id",
            "effective_at",
        ),
    )


class RecoveryCase(TimestampColumns, Base):
    __tablename__ = "recovery_cases"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(128))
    revenue_at_risk_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    diagnosis: Mapped[str | None] = mapped_column(String(128))
    diagnosis_confidence_basis_points: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_incident_id: Mapped[str | None] = mapped_column(String(128))
    next_evaluation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_reason: Mapped[str | None] = mapped_column(String(256))
    recovery_episode_key: Mapped[str | None] = mapped_column(String(64))
    latest_evidence_event_id: Mapped[str | None] = mapped_column(String(128))
    latest_evidence_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "latest_evidence_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "active_incident_id"],
            ["portfolio_incidents.merchant_id", "portfolio_incidents.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = '1.0'", name="ck_recovery_cases_schema_version"),
        CheckConstraint(
            "workflow_type IN ('FAILED_SUBSCRIPTION', 'PAYMENT_DEGRADATION', 'B2B_PROMISE_TO_PAY')",
            name="ck_recovery_cases_workflow_type",
        ),
        CheckConstraint(
            "subject_type IN ('PAYMENT', 'SUBSCRIPTION', 'INVOICE', 'PORTFOLIO_INCIDENT')",
            name="ck_recovery_cases_subject_type",
        ),
        CheckConstraint(
            "state IN ('DETECTED', 'DIAGNOSING', 'DECISION_PENDING', 'POLICY_CHECK', "
            "'READY', 'EXECUTING', 'VERIFYING', 'UNKNOWN', 'DEFERRED', 'ESCALATED', "
            "'RECOVERED', 'STOPPED')",
            name="ck_recovery_cases_state",
        ),
        CheckConstraint("state_version >= 1", name="ck_recovery_cases_state_version"),
        CheckConstraint("revenue_at_risk_minor >= 0", name="ck_recovery_cases_revenue_nonnegative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_recovery_cases_currency_iso"),
        CheckConstraint("retry_count >= 0", name="ck_recovery_cases_retry_nonnegative"),
        CheckConstraint("contact_count >= 0", name="ck_recovery_cases_contact_nonnegative"),
        CheckConstraint(
            "diagnosis_confidence_basis_points IS NULL OR "
            "diagnosis_confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_recovery_cases_diagnosis_confidence",
        ),
        CheckConstraint(
            "(state IN ('RECOVERED', 'STOPPED') AND terminal_reason IS NOT NULL) OR "
            "(state NOT IN ('RECOVERED', 'STOPPED') AND terminal_reason IS NULL)",
            name="ck_recovery_cases_terminal_reason",
        ),
        CheckConstraint(
            "recovery_episode_key IS NULL OR recovery_episode_key ~ '^[0-9a-f]{64}$'",
            name="ck_recovery_cases_episode_key",
        ),
        Index(
            "uq_recovery_cases_active_subject",
            "merchant_id",
            "workflow_type",
            "subject_type",
            "subject_id",
            unique=True,
            postgresql_where=text("state NOT IN ('RECOVERED', 'STOPPED')"),
        ),
        Index(
            "uq_recovery_cases_episode",
            "merchant_id",
            "workflow_type",
            "subject_type",
            "recovery_episode_key",
            unique=True,
            postgresql_where=text("recovery_episode_key IS NOT NULL"),
        ),
        Index(
            "ix_recovery_cases_due",
            "state",
            "next_evaluation_at",
            postgresql_where=text("state = 'DEFERRED'"),
        ),
        Index(
            "ix_recovery_cases_merchant_occurred",
            "merchant_id",
            "latest_evidence_occurred_at",
        ),
        Index(
            "ix_recovery_cases_customer_contact",
            "merchant_id",
            "customer_id",
            "state",
        ),
    )


class RecoveryCaseEvent(Base):
    __tablename__ = "recovery_case_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_case_id: Mapped[str | None] = mapped_column(String(128))
    normalized_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "normalized_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id", "normalized_event_id", name="uq_recovery_case_events_event"
        ),
        CheckConstraint(
            "disposition IN ('APPLIED', 'IGNORED_STALE', 'AUDIT_ONLY')",
            name="ck_recovery_case_events_disposition",
        ),
        Index("ix_recovery_case_events_case", "merchant_id", "recovery_case_id", "created_at"),
    )


class CaseTransition(Base):
    __tablename__ = "case_transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_state: Mapped[str] = mapped_column(String(32), nullable=False)
    after_state: Mapped[str] = mapped_column(String(32), nullable=False)
    before_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_detail: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    authoritative_evidence_reference: Mapped[str | None] = mapped_column(String(512))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id",
            "recovery_case_id",
            "after_version",
            name="uq_case_transitions_case_version",
        ),
        CheckConstraint(
            "after_version = before_version + 1", name="ck_case_transitions_version_increment"
        ),
        CheckConstraint(
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
            name="ck_case_transitions_allowed_edge",
        ),
        CheckConstraint(
            "(after_state = 'RECOVERED' AND authoritative_evidence_reference IS NOT NULL) OR "
            "(after_state <> 'RECOVERED')",
            name="ck_case_transitions_recovered_evidence",
        ),
        Index("ix_case_transitions_case", "merchant_id", "recovery_case_id", "after_version"),
    )


class HumanReview(Base):
    __tablename__ = "human_reviews"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_references: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_detail: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(128))
    rationale: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "action_fingerprint ~ '^[0-9a-f]{64}$' AND policy_digest ~ '^[0-9a-f]{64}$'",
            name="ck_human_reviews_digests",
        ),
        CheckConstraint(
            "status IN ('REQUESTED', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name="ck_human_reviews_status",
        ),
        CheckConstraint("expires_at > requested_at", name="ck_human_reviews_expiry"),
        CheckConstraint(
            "decided_at IS NULL OR ("
            "status IN ('APPROVED', 'REJECTED') AND decided_at >= requested_at "
            "AND decided_at < expires_at) OR (status = 'EXPIRED' AND decided_at >= expires_at)",
            name="ck_human_reviews_decision_chronology",
        ),
        CheckConstraint(
            "(status = 'REQUESTED' AND reviewer_id IS NULL AND rationale IS NULL AND "
            "decided_at IS NULL) OR (status <> 'REQUESTED' AND reviewer_id IS NOT NULL AND "
            "rationale IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_human_reviews_decision_metadata",
        ),
        Index("ix_human_reviews_case_status", "merchant_id", "recovery_case_id", "status"),
    )


class ModelPrediction(Base):
    __tablename__ = "model_predictions"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(128), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("merchant_id", "run_id", "node", name="uq_model_predictions_run_node"),
        CheckConstraint(
            "node IN ('DIAGNOSIS_ASSISTANCE', 'STRATEGY_GENERATION', 'RANKING', "
            "'EXPLANATION', 'GRAPH')",
            name="ck_model_predictions_node",
        ),
        CheckConstraint("status IN ('SUCCEEDED', 'FALLBACK')", name="ck_model_predictions_status"),
        CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$'", name="ck_model_predictions_input_digest"
        ),
        CheckConstraint(
            "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0",
            name="ck_model_predictions_usage_nonnegative",
        ),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND failure_code IS NULL) OR "
            "(status = 'FALLBACK' AND failure_code IS NOT NULL)",
            name="ck_model_predictions_failure_metadata",
        ),
        Index(
            "ix_model_predictions_case_created",
            "merchant_id",
            "recovery_case_id",
            "created_at",
        ),
    )


class DecisionReceipt(Base):
    __tablename__ = "decision_receipts"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_references: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    candidate_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, nullable=False)
    selected_action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    policy_result: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_reason_codes: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    version_bundle: Mapped[dict[str, str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    human_review_id: Mapped[str | None] = mapped_column(String(128))
    resulting_action_id: Mapped[str | None] = mapped_column(String(128))
    resulting_state: Mapped[str] = mapped_column(String(32), nullable=False)
    audit_entry_id: Mapped[str | None] = mapped_column(String(128))
    model_prediction_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    scoring_model_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="NOT_APPLICABLE"
    )
    scoring_feature_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="NOT_APPLICABLE"
    )
    scoring_economics_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="NOT_APPLICABLE"
    )
    scoring_artifact_classification: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NOT_APPLICABLE"
    )
    scoring_fallback_reason: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "human_review_id"],
            ["human_reviews.merchant_id", "human_reviews.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = '1.0'", name="ck_decision_receipts_schema_version"),
        CheckConstraint(
            "policy_result IN ('PROCEED', 'DEFER', 'SKIP', 'STOP', 'REQUIRE_HUMAN')",
            name="ck_decision_receipts_policy_result",
        ),
        CheckConstraint(
            "resulting_state IN ('READY', 'DEFERRED', 'DECISION_PENDING', 'ESCALATED', 'STOPPED')",
            name="ck_decision_receipts_resulting_state",
        ),
        CheckConstraint(
            "scoring_artifact_classification IN ('SYNTHETIC', 'PRODUCTION', 'NOT_APPLICABLE')",
            name="ck_decision_receipts_scoring_classification",
        ),
        Index("ix_decision_receipts_case", "merchant_id", "recovery_case_id", "created_at"),
    )


class RecoveryAction(TimestampColumns, Base):
    """Policy-authorized action and durable execution outbox row."""

    __tablename__ = "recovery_actions"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    logical_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execute_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_object_id: Mapped[str | None] = mapped_column(String(128))
    unknown_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "decision_receipt_id"],
            ["decision_receipts.merchant_id", "decision_receipts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("idempotency_key", name="uq_recovery_actions_idempotency_key"),
        CheckConstraint("schema_version = '1.0'", name="ck_recovery_actions_schema_version"),
        CheckConstraint(
            "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_recovery_actions_status",
        ),
        CheckConstraint("logical_attempt >= 1", name="ck_recovery_actions_logical_attempt"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_recovery_actions_attempt_bounds",
        ),
        CheckConstraint(
            "execute_after >= authorized_at AND next_attempt_at >= authorized_at",
            name="ck_recovery_actions_schedule",
        ),
        CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_recovery_actions_lease_pair",
        ),
        CheckConstraint(
            "(status = 'UNKNOWN' AND unknown_since IS NOT NULL AND "
            "reconciliation_deadline IS NOT NULL) OR "
            "(status <> 'UNKNOWN' AND unknown_since IS NULL)",
            name="ck_recovery_actions_unknown_metadata",
        ),
        Index(
            "uq_recovery_actions_active_equivalent",
            "merchant_id",
            "recovery_case_id",
            "action_type",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'SUCCEEDED', 'UNKNOWN')"),
        ),
        Index(
            "ix_recovery_actions_due",
            "status",
            "next_attempt_at",
            postgresql_where=text("status = 'PENDING' AND dead_lettered_at IS NULL"),
        ),
        Index(
            "ix_recovery_actions_unknown",
            "status",
            "reconciliation_deadline",
            postgresql_where=text("status = 'UNKNOWN'"),
        ),
    )


class CustomerIntervention(TimestampColumns, Base):
    """One durable cross-playbook customer contact coordination lease."""

    __tablename__ = "customer_interventions"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    coordinated_case_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "owner_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "recovery_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id", "recovery_action_id", name="uq_customer_interventions_action"
        ),
        CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="ck_customer_interventions_status"),
        CheckConstraint("cooldown_until > created_at", name="ck_customer_interventions_cooldown"),
        CheckConstraint(
            "jsonb_typeof(coordinated_case_ids) = 'array' "
            "AND jsonb_array_length(coordinated_case_ids) > 0",
            name="ck_customer_interventions_case_ids",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND closed_at IS NULL AND close_reason IS NULL) OR "
            "(status = 'CLOSED' AND closed_at IS NOT NULL AND close_reason IS NOT NULL)",
            name="ck_customer_interventions_close_metadata",
        ),
        Index(
            "uq_customer_interventions_active_customer",
            "merchant_id",
            "customer_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_customer_interventions_maintenance",
            "status",
            "cooldown_until",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class ActionAttempt(Base):
    __tablename__ = "action_attempts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_status: Mapped[str | None] = mapped_column(String(32))
    response_category: Mapped[str | None] = mapped_column(String(64))
    provider_object_id: Mapped[str | None] = mapped_column(String(128))
    provider_status_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128))
    response_reference: Mapped[str | None] = mapped_column(String(512))
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id",
            "recovery_action_id",
            "attempt_number",
            name="uq_action_attempts_action_number",
        ),
        UniqueConstraint("request_id", name="uq_action_attempts_request_id"),
        CheckConstraint("attempt_number >= 1", name="ck_action_attempts_number"),
        CheckConstraint(
            "outcome_status IS NULL OR outcome_status IN "
            "('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_action_attempts_outcome_status",
        ),
        CheckConstraint(
            "(completed_at IS NULL AND outcome_status IS NULL) OR "
            "(completed_at IS NOT NULL AND outcome_status IS NOT NULL AND "
            "completed_at >= started_at)",
            name="ck_action_attempts_completion",
        ),
        Index("ix_action_attempts_action", "merchant_id", "recovery_action_id", "attempt_number"),
    )


class VerifiedOutcome(Base):
    __tablename__ = "verified_outcomes"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome_status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_source: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(512))
    provider_object_id: Mapped[str | None] = mapped_column(String(128))
    recovered_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = '1.0'", name="ck_verified_outcomes_schema_version"),
        CheckConstraint(
            "outcome_status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'UNKNOWN')",
            name="ck_verified_outcomes_status",
        ),
        CheckConstraint(
            "evidence_source IN ('SIGNED_WEBHOOK', 'PROVIDER_LOOKUP', 'PROVIDER_RESPONSE', "
            "'SIMULATOR', 'NONE')",
            name="ck_verified_outcomes_evidence_source",
        ),
        CheckConstraint(
            "recovered_amount_minor >= 0", name="ck_verified_outcomes_amount_nonnegative"
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_verified_outcomes_currency_iso"),
        CheckConstraint(
            "recovered_amount_minor = 0 OR (outcome_status = 'SUCCEEDED' AND "
            "is_authoritative AND evidence_reference IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_verified_outcomes_recovered_authority",
        ),
        CheckConstraint(
            "outcome_status <> 'UNKNOWN' OR (NOT is_authoritative AND "
            "recovered_amount_minor = 0 AND verified_at IS NULL)",
            name="ck_verified_outcomes_unknown",
        ),
        Index(
            "uq_verified_outcomes_authoritative_success",
            "merchant_id",
            "recovery_action_id",
            unique=True,
            postgresql_where=text("is_authoritative AND outcome_status = 'SUCCEEDED'"),
        ),
        Index(
            "ix_verified_outcomes_metrics",
            "merchant_id",
            "currency",
            "verified_at",
            postgresql_where=text(
                "is_authoritative AND outcome_status = 'SUCCEEDED' AND recovered_amount_minor > 0"
            ),
        ),
    )


class CommunicationConsent(TimestampColumns, Base):
    __tablename__ = "communication_consents"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "channel IN ('EMAIL', 'SMS', 'WHATSAPP')", name="ck_communication_consents_channel"
        ),
        CheckConstraint(
            "state IN ('GRANTED', 'DENIED', 'UNKNOWN')", name="ck_communication_consents_state"
        ),
    )


class PortfolioIncident(Base):
    __tablename__ = "portfolio_incidents"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(32))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    dimension_key: Mapped[str] = mapped_column(String(384), nullable=False, default="LEGACY")
    payment_method: Mapped[str | None] = mapped_column(String(128))
    issuer_family: Mapped[str | None] = mapped_column(String(128))
    error_family: Mapped[str | None] = mapped_column(String(128))
    baseline_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_failure_rate_basis_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    current_failure_rate_basis_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    threshold_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="phase6-playbooks-1.0"
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    clear_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_reason: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        CheckConstraint(
            "scope IN ('PAYMENT_RAIL', 'GATEWAY', 'ISSUER', 'CONTACT_CHANNEL', 'ALL_AUTOMATION')",
            name="ck_portfolio_incidents_scope",
        ),
        CheckConstraint("status IN ('ACTIVE', 'RESOLVED')", name="ck_portfolio_incidents_status"),
        CheckConstraint("ends_at > starts_at", name="ck_portfolio_incidents_window"),
        CheckConstraint(
            "baseline_total >= 0 AND baseline_failures >= 0 "
            "AND baseline_failures <= baseline_total "
            "AND current_total >= 0 AND current_failures >= 0 "
            "AND current_failures <= current_total",
            name="ck_portfolio_incidents_counts",
        ),
        CheckConstraint(
            "baseline_failure_rate_basis_points BETWEEN 0 AND 10000 "
            "AND current_failure_rate_basis_points BETWEEN 0 AND 10000",
            name="ck_portfolio_incidents_rates",
        ),
        CheckConstraint("clear_window_count >= 0", name="ck_portfolio_incidents_clear_windows"),
        CheckConstraint(
            "(status = 'ACTIVE' AND resolved_at IS NULL AND resolution_reason IS NULL) OR "
            "(status = 'RESOLVED' AND resolved_at IS NOT NULL "
            "AND resolution_reason IS NOT NULL)",
            name="ck_portfolio_incidents_resolution",
        ),
        CheckConstraint(
            "(scope = 'CONTACT_CHANNEL' AND channel IN ('EMAIL', 'SMS', 'WHATSAPP')) OR "
            "(scope <> 'CONTACT_CHANNEL' AND channel IS NULL)",
            name="ck_portfolio_incidents_channel_scope",
        ),
        Index("ix_portfolio_incidents_active", "merchant_id", "starts_at", "ends_at"),
        Index(
            "uq_portfolio_incidents_active_dimension",
            "merchant_id",
            "dimension_key",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class CustomerResponse(Base):
    """Redacted customer reply evidence and its validated structured extraction."""

    __tablename__ = "customer_responses"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_response_id: Mapped[str] = mapped_column(String(256), nullable=False)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    promised_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    confidence_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "invoice_id"],
            ["invoices.merchant_id", "invoices.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("merchant_id", "source_response_id", name="uq_customer_responses_source"),
        CheckConstraint("body_sha256 ~ '^[0-9a-f]{64}$'", name="ck_responses_body_digest"),
        CheckConstraint(
            "intent IN ('PROMISE_TO_PAY', 'DISPUTE', 'ALREADY_PAID', 'NEEDS_HELP', 'UNKNOWN')",
            name="ck_responses_intent",
        ),
        CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_responses_confidence",
        ),
        CheckConstraint(
            "(intent = 'PROMISE_TO_PAY' AND promised_for IS NOT NULL "
            "AND amount_minor > 0 AND currency ~ '^[A-Z]{3}$') OR "
            "(intent <> 'PROMISE_TO_PAY' AND promised_for IS NULL "
            "AND amount_minor IS NULL AND currency IS NULL)",
            name="ck_responses_promise_terms",
        ),
    )


class PromiseToPay(Base):
    __tablename__ = "promises_to_pay"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_response_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    promised_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminder_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_confidence_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_action_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "reminder_action_id"],
            ["recovery_actions.merchant_id", "recovery_actions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "invoice_id"],
            ["invoices.merchant_id", "invoices.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "customer_response_id"],
            ["customer_responses.merchant_id", "customer_responses.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("merchant_id", "customer_response_id", name="uq_promises_response"),
        CheckConstraint("amount_minor > 0", name="ck_promises_amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_promises_currency_iso"),
        CheckConstraint(
            "status IN ('ACTIVE', 'FULFILLED', 'BROKEN', 'DISPUTED', 'CANCELLED')",
            name="ck_promises_status",
        ),
        CheckConstraint(
            "extraction_confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_promises_confidence",
        ),
        CheckConstraint(
            "(status = 'FULFILLED' AND fulfilled_at IS NOT NULL) OR status <> 'FULFILLED'",
            name="ck_promises_fulfilled_at",
        ),
        CheckConstraint(
            "(status = 'BROKEN' AND broken_at IS NOT NULL) OR status <> 'BROKEN'",
            name="ck_promises_broken_at",
        ),
        Index(
            "ix_promises_due_reminder",
            "status",
            "reminder_at",
            postgresql_where=text("status = 'ACTIVE' AND reminder_action_id IS NULL"),
        ),
        Index(
            "ix_promises_due_break",
            "status",
            "promised_for",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class ReceivableEscalation(Base):
    __tablename__ = "receivable_escalations"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_response_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "invoice_id"],
            ["invoices.merchant_id", "invoices.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "customer_response_id"],
            ["customer_responses.merchant_id", "customer_responses.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "merchant_id", "customer_response_id", name="uq_receivable_escalations_response"
        ),
        CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_escalations_status"),
    )


class PaymentOutcomeObservation(Base):
    __tablename__ = "payment_outcome_observations"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer_family: Mapped[str] = mapped_column(String(128), nullable=False)
    error_family: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "merchant_id", "source_event_id", name="uq_payment_observations_source_event"
        ),
        Index(
            "ix_payment_observations_dimension_time",
            "merchant_id",
            "payment_method",
            "issuer_family",
            "error_family",
            "occurred_at",
        ),
    )


class IncidentCaseLink(Base):
    __tablename__ = "incident_case_links"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    recovery_case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resume_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["merchant_id", "incident_id"],
            ["portfolio_incidents.merchant_id", "portfolio_incidents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "resume_after IS NULL OR resume_after >= attached_at",
            name="ck_incident_case_links_resume_order",
        ),
        Index("ix_incident_case_links_resume", "merchant_id", "resume_after", "resumed_at"),
    )


class SimulationSession(Base):
    """Durable Test Mode checkout session; never contributes to production metrics."""

    __tablename__ = "simulation_sessions"

    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False)
    flow_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subscription_id: Mapped[str | None] = mapped_column(String(128))
    provider_event_id: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    classification: Mapped[str] = mapped_column(String(16), nullable=False, default="SYNTHETIC")
    generator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", name="uq_simulation_sessions_public_id"),
        UniqueConstraint("merchant_id", "payment_id", name="uq_simulation_sessions_payment"),
        UniqueConstraint("merchant_id", "provider_event_id", name="uq_simulation_sessions_event"),
        CheckConstraint(
            "scenario IN ('SUCCESS', 'INSUFFICIENT_FUNDS', 'AUTHENTICATION_FAILURE', "
            "'ISSUER_OUTAGE', 'TIMEOUT')",
            name="ck_simulation_sessions_scenario",
        ),
        CheckConstraint(
            "flow_type IN ('ONE_TIME', 'SUBSCRIPTION')",
            name="ck_simulation_sessions_flow",
        ),
        CheckConstraint("amount_minor > 0", name="ck_simulation_sessions_amount"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_simulation_sessions_currency"),
        CheckConstraint(
            "status IN ('CREATED', 'SUBMITTED', 'EXPIRED')",
            name="ck_simulation_sessions_status",
        ),
        CheckConstraint(
            "classification = 'SYNTHETIC'", name="ck_simulation_sessions_classification"
        ),
        CheckConstraint("expires_at > created_at", name="ck_simulation_sessions_expiry"),
        CheckConstraint(
            "(status = 'CREATED' AND attempted_at IS NULL AND provider_event_id IS NULL) OR "
            "(status = 'SUBMITTED' AND attempted_at IS NOT NULL "
            "AND provider_event_id IS NOT NULL) OR "
            "(status = 'EXPIRED' AND provider_event_id IS NULL)",
            name="ck_simulation_sessions_lifecycle",
        ),
        Index("ix_simulation_sessions_expiry", "status", "expires_at"),
    )


class EventDispatch(Base):
    __tablename__ = "event_dispatches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    webhook_event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    normalized_event_id: Mapped[str | None] = mapped_column(String(128))
    queue_name: Mapped[str] = mapped_column(String(128), nullable=False, default="event_ingestion")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broker_task_id: Mapped[str | None] = mapped_column(String(128))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replayed_by: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["merchant_id", "webhook_event_id"],
            ["webhook_events.merchant_id", "webhook_events.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["merchant_id", "normalized_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("webhook_event_id", name="uq_event_dispatches_webhook_event"),
        CheckConstraint(
            "state IN ('PENDING', 'PROCESSING', 'PUBLISHED', 'RETRY_SCHEDULED', "
            "'SUCCEEDED', 'DEAD_LETTER')",
            name="ck_event_dispatches_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_event_dispatches_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_event_dispatches_max_attempts_positive"),
        CheckConstraint("replay_count >= 0", name="ck_event_dispatches_replay_nonnegative"),
        CheckConstraint(
            "attempt_count <= max_attempts", name="ck_event_dispatches_attempt_within_limit"
        ),
        CheckConstraint(
            "state <> 'DEAD_LETTER' OR dead_lettered_at IS NOT NULL",
            name="ck_event_dispatches_dead_letter_timestamp",
        ),
        Index("ix_event_dispatches_claim", "state", "available_at", "created_at"),
        Index("ix_event_dispatches_merchant_state", "merchant_id", "state"),
    )
