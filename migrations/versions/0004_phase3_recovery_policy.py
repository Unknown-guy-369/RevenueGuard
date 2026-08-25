"""Add the deterministic recovery policy and case decision tables.

Revision ID: 0004_phase3_recovery_policy
Revises: 0003_phase2_dead_letter_replay
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase3_recovery_policy"
down_revision: str | Sequence[str] | None = "0003_phase2_dead_letter_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_TABLE = "merchant_policy_versions"
_CASE_TABLE = "recovery_cases"
_CASE_EVENT_TABLE = "recovery_case_events"
_TRANSITION_TABLE = "case_transitions"
_REVIEW_TABLE = "human_reviews"
_RECEIPT_TABLE = "decision_receipts"
_CONSENT_TABLE = "communication_consents"
_INCIDENT_TABLE = "portfolio_incidents"


def upgrade() -> None:
    op.create_table(
        _POLICY_TABLE,
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("published_by", sa.String(length=128), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_policy_versions_content_sha256"
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "version"),
        sa.UniqueConstraint(
            "merchant_id",
            "effective_at",
            name="uq_merchant_policy_versions_effective_at",
        ),
        sa.UniqueConstraint(
            "merchant_id",
            "version",
            "content_sha256",
            name="uq_policy_versions_identity_digest",
        ),
    )
    op.create_index(
        "ix_merchant_policy_versions_effective",
        _POLICY_TABLE,
        ["merchant_id", "effective_at"],
        unique=False,
    )

    op.create_table(
        _CASE_TABLE,
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=True),
        sa.Column("revenue_at_risk_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("diagnosis", sa.String(length=128), nullable=True),
        sa.Column("diagnosis_confidence_basis_points", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("contact_count", sa.Integer(), nullable=False),
        sa.Column("active_incident_id", sa.String(length=128), nullable=True),
        sa.Column("next_evaluation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(length=256), nullable=True),
        sa.Column("recovery_episode_key", sa.String(length=64), nullable=True),
        sa.Column("latest_evidence_event_id", sa.String(length=128), nullable=True),
        sa.Column("latest_evidence_occurred_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("schema_version = '1.0'", name="ck_recovery_cases_schema_version"),
        sa.CheckConstraint(
            "state IN ('DETECTED', 'DIAGNOSING', 'DECISION_PENDING', 'POLICY_CHECK', "
            "'READY', 'EXECUTING', 'VERIFYING', 'UNKNOWN', 'DEFERRED', 'ESCALATED', "
            "'RECOVERED', 'STOPPED')",
            name="ck_recovery_cases_state",
        ),
        sa.CheckConstraint("state_version >= 1", name="ck_recovery_cases_state_version"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_recovery_cases_currency_iso"),
        sa.CheckConstraint(
            "revenue_at_risk_minor >= 0", name="ck_recovery_cases_revenue_nonnegative"
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_recovery_cases_retry_nonnegative"),
        sa.CheckConstraint("contact_count >= 0", name="ck_recovery_cases_contact_nonnegative"),
        sa.CheckConstraint(
            "diagnosis_confidence_basis_points IS NULL OR "
            "diagnosis_confidence_basis_points BETWEEN 0 AND 10000",
            name="ck_recovery_cases_diagnosis_confidence",
        ),
        sa.CheckConstraint(
            "(state IN ('RECOVERED', 'STOPPED') AND terminal_reason IS NOT NULL) OR "
            "(state NOT IN ('RECOVERED', 'STOPPED') AND terminal_reason IS NULL)",
            name="ck_recovery_cases_terminal_reason",
        ),
        sa.CheckConstraint(
            "recovery_episode_key IS NULL OR recovery_episode_key ~ '^[0-9a-f]{64}$'",
            name="ck_recovery_cases_episode_key",
        ),
        sa.CheckConstraint(
            "workflow_type IN ('FAILED_SUBSCRIPTION', 'PAYMENT_DEGRADATION', 'B2B_PROMISE_TO_PAY')",
            name="ck_recovery_cases_workflow_type",
        ),
        sa.CheckConstraint(
            "subject_type IN ('PAYMENT', 'SUBSCRIPTION', 'INVOICE', 'PORTFOLIO_INCIDENT')",
            name="ck_recovery_cases_subject_type",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "latest_evidence_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
    )
    op.create_index(
        "ix_recovery_cases_due",
        _CASE_TABLE,
        ["state", "next_evaluation_at"],
        unique=False,
        postgresql_where=sa.text("state = 'DEFERRED'"),
    )
    op.create_index(
        "ix_recovery_cases_merchant_occurred",
        _CASE_TABLE,
        ["merchant_id", "latest_evidence_occurred_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_recovery_cases_active_subject "
            "ON recovery_cases (merchant_id, workflow_type, subject_type, subject_id) "
            "WHERE state NOT IN ('RECOVERED', 'STOPPED')"
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_recovery_cases_episode "
            "ON recovery_cases (merchant_id, workflow_type, subject_type, subject_id, "
            "recovery_episode_key) WHERE recovery_episode_key IS NOT NULL"
        )
    )

    op.create_table(
        _CASE_EVENT_TABLE,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=True),
        sa.Column("normalized_event_id", sa.String(length=128), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('APPLIED', 'IGNORED_STALE', 'AUDIT_ONLY')",
            name="ck_recovery_case_events_disposition",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "normalized_event_id"],
            ["normalized_events.merchant_id", "normalized_events.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id",
            "normalized_event_id",
            name="uq_recovery_case_events_event",
        ),
    )
    op.create_index(
        "ix_recovery_case_events_case",
        _CASE_EVENT_TABLE,
        ["merchant_id", "recovery_case_id", "created_at"],
        unique=False,
    )

    op.create_table(
        _TRANSITION_TABLE,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("before_state", sa.String(length=32), nullable=False),
        sa.Column("after_state", sa.String(length=32), nullable=False),
        sa.Column("before_version", sa.Integer(), nullable=False),
        sa.Column("after_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("authoritative_evidence_reference", sa.String(length=512), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "after_version = before_version + 1",
            name="ck_case_transitions_version_increment",
        ),
        sa.CheckConstraint(
            "(before_state = 'DETECTED' AND after_state IN ('DIAGNOSING')) OR "
            "(before_state = 'DIAGNOSING' AND after_state IN ('DECISION_PENDING')) OR "
            "(before_state = 'DECISION_PENDING' AND after_state IN ('POLICY_CHECK')) OR "
            "(before_state = 'POLICY_CHECK' AND after_state IN "
            "('READY', 'DEFERRED', 'DECISION_PENDING', 'ESCALATED', 'STOPPED')) OR "
            "(before_state = 'READY' AND after_state IN ('EXECUTING')) OR "
            "(before_state = 'EXECUTING' AND after_state IN ('VERIFYING', 'UNKNOWN')) OR "
            "(before_state = 'VERIFYING' AND after_state IN "
            "('RECOVERED', 'DECISION_PENDING', 'STOPPED')) OR "
            "(before_state = 'UNKNOWN' AND after_state IN ('VERIFYING', 'ESCALATED')) OR "
            "(before_state = 'DEFERRED' AND after_state IN ('DECISION_PENDING')) OR "
            "(before_state = 'ESCALATED' AND after_state IN ('DECISION_PENDING', 'STOPPED'))",
            name="ck_case_transitions_allowed_edge",
        ),
        sa.CheckConstraint(
            "(after_state = 'RECOVERED' AND authoritative_evidence_reference IS NOT NULL) OR "
            "(after_state <> 'RECOVERED')",
            name="ck_case_transitions_recovered_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "recovery_case_id"],
            ["recovery_cases.merchant_id", "recovery_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id",
            "recovery_case_id",
            "after_version",
            name="uq_case_transitions_case_version",
        ),
    )
    op.create_index(
        "ix_case_transitions_case",
        _TRANSITION_TABLE,
        ["merchant_id", "recovery_case_id", "after_version"],
        unique=False,
    )

    op.create_table(
        _REVIEW_TABLE,
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("action_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("proposed_action_type", sa.String(length=64), nullable=False),
        sa.Column(
            "evidence_references",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("risk_detail", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_id", sa.String(length=128), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action_fingerprint ~ '^[0-9a-f]{64}$' AND policy_digest ~ '^[0-9a-f]{64}$'",
            name="ck_human_reviews_digests",
        ),
        sa.CheckConstraint("expires_at > requested_at", name="ck_human_reviews_expiry"),
        sa.CheckConstraint(
            "decided_at IS NULL OR ("
            "status IN ('APPROVED', 'REJECTED') AND decided_at >= requested_at "
            "AND decided_at < expires_at) OR (status = 'EXPIRED' AND decided_at >= expires_at)",
            name="ck_human_reviews_decision_chronology",
        ),
        sa.CheckConstraint(
            "(status = 'REQUESTED' AND reviewer_id IS NULL AND rationale IS NULL AND "
            "decided_at IS NULL) OR (status <> 'REQUESTED' AND reviewer_id IS NOT NULL AND "
            "rationale IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_human_reviews_decision_metadata",
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED', 'APPROVED', 'REJECTED', 'EXPIRED')",
            name="ck_human_reviews_status",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
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
        "ix_human_reviews_case_status",
        _REVIEW_TABLE,
        ["merchant_id", "recovery_case_id", "status"],
        unique=False,
    )

    op.create_table(
        _RECEIPT_TABLE,
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("recovery_case_id", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "evidence_references",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "candidate_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("selected_action_type", sa.String(length=64), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("policy_result", sa.String(length=32), nullable=False),
        sa.Column(
            "policy_reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column(
            "version_bundle",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("human_review_id", sa.String(length=128), nullable=True),
        sa.Column("resulting_action_id", sa.String(length=128), nullable=True),
        sa.Column("resulting_state", sa.String(length=32), nullable=False),
        sa.Column("audit_entry_id", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version = '1.0'", name="ck_decision_receipts_schema_version"),
        sa.CheckConstraint(
            "policy_result IN ('PROCEED', 'DEFER', 'SKIP', 'STOP', 'REQUIRE_HUMAN')",
            name="ck_decision_receipts_policy_result",
        ),
        sa.CheckConstraint(
            "resulting_state IN ('READY', 'DEFERRED', 'DECISION_PENDING', 'ESCALATED', 'STOPPED')",
            name="ck_decision_receipts_resulting_state",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "human_review_id"],
            ["human_reviews.merchant_id", "human_reviews.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "policy_version"],
            ["merchant_policy_versions.merchant_id", "merchant_policy_versions.version"],
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
        "ix_decision_receipts_case",
        _RECEIPT_TABLE,
        ["merchant_id", "recovery_case_id", "created_at"],
        unique=False,
    )

    op.create_table(
        _CONSENT_TABLE,
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("opted_out", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
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
            "channel IN ('EMAIL', 'SMS', 'WHATSAPP')",
            name="ck_communication_consents_channel",
        ),
        sa.CheckConstraint(
            "state IN ('GRANTED', 'DENIED', 'UNKNOWN')",
            name="ck_communication_consents_state",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id", "customer_id"],
            ["customers.merchant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("merchant_id", "customer_id", "channel"),
    )

    op.create_table(
        _INCIDENT_TABLE,
        sa.Column("merchant_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('PAYMENT_RAIL', 'GATEWAY', 'ISSUER', 'CONTACT_CHANNEL', 'ALL_AUTOMATION')",
            name="ck_portfolio_incidents_scope",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_portfolio_incidents_window"),
        sa.CheckConstraint(
            "(scope = 'CONTACT_CHANNEL' AND channel IN ('EMAIL', 'SMS', 'WHATSAPP')) OR "
            "(scope <> 'CONTACT_CHANNEL' AND channel IS NULL)",
            name="ck_portfolio_incidents_channel_scope",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RESOLVED')",
            name="ck_portfolio_incidents_status",
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("merchant_id", "id"),
    )
    op.create_index(
        "ix_portfolio_incidents_active",
        _INCIDENT_TABLE,
        ["merchant_id", "starts_at", "ends_at"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_recovery_cases_active_incident",
        _CASE_TABLE,
        _INCIDENT_TABLE,
        ["merchant_id", "active_incident_id"],
        ["merchant_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                merchant RECORD;
            BEGIN
                FOR merchant IN SELECT id FROM merchants LOOP
                    INSERT INTO merchant_policy_versions (
                        merchant_id,
                        version,
                        snapshot,
                        content_sha256,
                        published_by,
                        effective_at,
                        created_at
                    )
                    VALUES (
                        merchant.id,
                        'phase3-conservative-default-1.0',
                        '{
                            "version": "phase3-conservative-default-1.0",
                            "features_version": "phase3-v1",
                            "allowed_actions": [
                                "DEFER_RETRY",
                                "CREATE_PAYMENT_LINK",
                                "REQUEST_PAYMENT_METHOD_UPDATE",
                                "SEND_REMINDER",
                                "SCHEDULE_PROMISE_REMINDER",
                                "PAUSE_RETRIES",
                                "RESUME_DEFERRED_CASE",
                                "ESCALATE_HUMAN",
                                "STOP_AUTOMATION",
                                "NO_ACTION"
                            ],
                            "retry_limit": 3,
                            "contact_limit": 2,
                            "currency": "INR",
                            "minimum_expected_net_recovery_minor": 100,
                            "human_review_amount_minor": 50000,
                            "minimum_confidence_basis_points": 5000,
                            "default_defer_seconds": 3600,
                            "timezone": "UTC",
                            "quiet_hours_start": "22:00:00",
                            "quiet_hours_end": "07:00:00",
                            "effective_at": "2026-08-25T00:00:00Z"
                        }'::jsonb,
                        '387fdd0a779b69b21775364c682e8c5a2957de461806af695b773fd7b78fcab2',
                        'MIGRATION',
                        '2026-08-25T00:00:00Z'::timestamptz,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT DO NOTHING;
                END LOOP;
            END $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_immutable_insert_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append-only table % cannot be modified', TG_TABLE_NAME;
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    for table in (_POLICY_TABLE, _TRANSITION_TABLE, _RECEIPT_TABLE):
        op.execute(
            sa.text(
                f"CREATE TRIGGER prevent_{table}_mutate "
                f"AFTER UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION trg_immutable_insert_only();"
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 3 recovery decisions and policy history are historical workflow evidence and are "
        "not removed."
    )
