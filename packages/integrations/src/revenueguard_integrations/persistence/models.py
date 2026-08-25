"""SQLAlchemy models for Phase 2 event ingestion and correlation.

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
    webhook_event_id: Mapped[str] = mapped_column(String(36), nullable=False)
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
