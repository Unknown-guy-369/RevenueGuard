"""Merchant-scoped, read-only dashboard contracts and query boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class DashboardPersistenceError(RuntimeError):
    """Authoritative dashboard state could not be read."""


class DashboardNotFoundError(LookupError):
    """A tenant-scoped dashboard resource does not exist."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardContext(_Contract):
    schema_version: Literal["1.0"] = "1.0"
    merchant_id: str
    merchant_display_name: str
    environment: Literal["TEST"] = "TEST"
    data_classification: Literal["TEST"] = "TEST"
    as_of: datetime


class CurrencyTotals(_Contract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    revenue_at_risk_minor: int = Field(ge=0)
    verified_recovered_minor: int = Field(ge=0)


class CaseSummary(_Contract):
    case_id: str
    state: str
    state_version: int = Field(ge=1)
    workflow_type: str
    subject_type: str
    subject_reference_masked: str
    customer_reference_masked: str | None
    revenue_at_risk_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    diagnosis: str | None
    diagnosis_confidence_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    retry_count: int = Field(ge=0)
    contact_count: int = Field(ge=0)
    classification: Literal["TEST", "SYNTHETIC"] = "TEST"
    updated_at: datetime


class DashboardCounts(_Contract):
    active_cases: int = Field(ge=0)
    recovered_cases: int = Field(ge=0)
    stopped_cases: int = Field(ge=0)
    unknown_cases: int = Field(ge=0)
    deferred_cases: int = Field(ge=0)
    escalated_cases: int = Field(ge=0)
    pending_reviews: int = Field(ge=0)
    pending_actions: int = Field(ge=0)
    decision_receipts: int = Field(ge=0)
    model_succeeded: int = Field(ge=0)
    model_fallback: int = Field(ge=0)


class DashboardOverview(_Contract):
    context: DashboardContext
    currency_totals: tuple[CurrencyTotals, ...]
    counts: DashboardCounts
    recent_cases: tuple[CaseSummary, ...]


class CaseList(_Contract):
    context: DashboardContext
    cases: tuple[CaseSummary, ...]
    total: int = Field(ge=0)


class TransitionItem(_Contract):
    transition_id: str
    from_state: str
    to_state: str
    reason_code: str
    reason_detail: str | None
    actor_reference_masked: str
    correlation_id: str
    policy_version: str
    authoritative_evidence_reference: str | None
    occurred_at: datetime
    case_version: int = Field(ge=1)


class DecisionItem(_Contract):
    decision_id: str
    selected_action_type: str
    explanation: str
    policy_result: str
    policy_reason_codes: tuple[str, ...]
    policy_version: str
    resulting_state: str
    resulting_action_id: str | None
    model_prediction_ids: tuple[str, ...]
    created_at: datetime


class PredictionItem(_Contract):
    prediction_id: str
    node: str
    status: Literal["SUCCEEDED", "FALLBACK"]
    model_version: str
    prompt_version: str
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    failure_code: str | None
    created_at: datetime


class ActionItem(_Contract):
    action_id: str
    action_type: str
    target_reference_masked: str
    logical_attempt: int = Field(ge=1)
    idempotency_key: str
    status: str
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    policy_version: str
    authorized_at: datetime
    unknown_since: datetime | None
    last_error_code: str | None


class OutcomeItem(_Contract):
    outcome_id: str
    action_id: str
    status: str
    is_authoritative: bool
    recovered_amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    evidence_source: str
    evidence_reference: str | None
    provider_reference_masked: str | None
    reason_code: str | None
    observed_at: datetime
    verified_at: datetime | None


class ReviewItem(_Contract):
    review_id: str
    status: str
    proposed_action_type: str
    reason_code: str
    risk_detail: str
    policy_version: str
    requested_at: datetime
    expires_at: datetime
    reviewed_at: datetime | None
    reviewer_reference_masked: str | None
    rationale: str | None


class CaseDetail(_Contract):
    context: DashboardContext
    case: CaseSummary
    transitions: tuple[TransitionItem, ...]
    decisions: tuple[DecisionItem, ...]
    predictions: tuple[PredictionItem, ...]
    actions: tuple[ActionItem, ...]
    outcomes: tuple[OutcomeItem, ...]
    reviews: tuple[ReviewItem, ...]


class OperationsHealth(_Contract):
    context: DashboardContext
    status: Literal["HEALTHY", "DEGRADED"]
    pending_events: int = Field(ge=0)
    dead_letter_events: int = Field(ge=0)
    pending_actions: int = Field(ge=0)
    unknown_actions: int = Field(ge=0)


class DashboardQueryService(Protocol):
    async def overview(self, merchant_id: str) -> DashboardOverview: ...

    async def list_cases(
        self,
        merchant_id: str,
        *,
        states: tuple[str, ...],
        limit: int,
    ) -> CaseList: ...

    async def case_detail(self, merchant_id: str, case_id: str) -> CaseDetail: ...

    async def operations_health(self, merchant_id: str) -> OperationsHealth: ...


class UnavailableDashboardQueryService:
    async def overview(self, merchant_id: str) -> DashboardOverview:
        del merchant_id
        raise DashboardPersistenceError("dashboard persistence is not configured")

    async def list_cases(
        self,
        merchant_id: str,
        *,
        states: tuple[str, ...],
        limit: int,
    ) -> CaseList:
        del merchant_id, states, limit
        raise DashboardPersistenceError("dashboard persistence is not configured")

    async def case_detail(self, merchant_id: str, case_id: str) -> CaseDetail:
        del merchant_id, case_id
        raise DashboardPersistenceError("dashboard persistence is not configured")

    async def operations_health(self, merchant_id: str) -> OperationsHealth:
        del merchant_id
        raise DashboardPersistenceError("dashboard persistence is not configured")
