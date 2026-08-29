"""Typed contracts for the full merchant dashboard and Test Mode simulator."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from revenueguard_api.dashboard import DashboardContext


class MerchantDashboardConflictError(RuntimeError):
    """A mutable dashboard command conflicts with current authoritative state."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessCurrencyTotals(_Contract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    gross_volume_minor: int = Field(ge=0)
    collected_minor: int = Field(ge=0)
    failed_value_minor: int = Field(ge=0)
    verified_recovered_minor: int = Field(ge=0)
    payment_count: int = Field(ge=0)
    successful_payment_count: int = Field(ge=0)
    failed_payment_count: int = Field(ge=0)
    success_rate_basis_points: int = Field(ge=0, le=10_000)


class PaymentMethodShare(_Contract):
    payment_method: str
    payment_count: int = Field(ge=0)
    share_basis_points: int = Field(ge=0, le=10_000)


class BusinessOverview(_Contract):
    context: DashboardContext
    since: datetime
    currency_totals: tuple[BusinessCurrencyTotals, ...]
    payment_methods: tuple[PaymentMethodShare, ...]
    settlement_data_available: Literal[False] = False


class RevenueSeriesPoint(_Contract):
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    collected_minor: int = Field(ge=0)
    failed_minor: int = Field(ge=0)
    verified_recovered_minor: int = Field(ge=0)


class RevenueSeries(_Contract):
    context: DashboardContext
    since: datetime
    points: tuple[RevenueSeriesPoint, ...]


class PaymentSummary(_Contract):
    payment_id: str
    provider_reference_masked: str
    customer_reference_masked: str | None
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    payment_method: str | None
    failure_category: str | None
    recovery_case_id: str | None
    recovery_state: str | None
    classification: Literal["TEST", "SYNTHETIC"]
    occurred_at: datetime


class PaymentList(_Contract):
    context: DashboardContext
    payments: tuple[PaymentSummary, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class PaymentDetail(_Contract):
    context: DashboardContext
    payment: PaymentSummary
    order_reference_masked: str | None
    diagnosis: str | None
    next_evaluation_at: datetime | None
    updated_at: datetime


class RecoveryCurrencyTotals(_Contract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    revenue_at_risk_minor: int = Field(ge=0)
    verified_gross_recovered_minor: int = Field(ge=0)
    recovery_cost_minor: int | None = Field(default=None, ge=0)
    verified_net_recovered_minor: int | None = Field(default=None, ge=0)


class RecoveryOverview(_Contract):
    context: DashboardContext
    currency_totals: tuple[RecoveryCurrencyTotals, ...]
    active_cases: int = Field(ge=0)
    deferred_cases: int = Field(ge=0)
    unknown_cases: int = Field(ge=0)
    pending_reviews: int = Field(ge=0)
    active_incidents: int = Field(ge=0)
    cost_data_available: Literal[False] = False


class IncidentSummary(_Contract):
    incident_id: str
    status: str
    payment_method: str | None
    issuer_family: str | None
    error_family: str | None
    baseline_failure_rate_basis_points: int = Field(ge=0, le=10_000)
    current_failure_rate_basis_points: int = Field(ge=0, le=10_000)
    affected_payments: int = Field(ge=0)
    paused_cases: int = Field(ge=0)
    healthy_windows: int = Field(ge=0)
    threshold_version: str
    starts_at: datetime
    ends_at: datetime
    resolved_at: datetime | None


class IncidentList(_Contract):
    context: DashboardContext
    incidents: tuple[IncidentSummary, ...]
    total: int = Field(ge=0)


class ReviewSummary(_Contract):
    review_id: str
    case_id: str
    customer_reference_masked: str | None
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    proposed_action_type: str
    diagnosis: str | None
    confidence_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    reason_code: str
    risk_detail: str
    policy_version: str
    classification: Literal["TEST", "SYNTHETIC"]
    requested_at: datetime
    expires_at: datetime


class ReviewList(_Contract):
    context: DashboardContext
    reviews: tuple[ReviewSummary, ...]
    total: int = Field(ge=0)


class ReviewDecisionRequest(_Contract):
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=3, max_length=1_000)


class ReviewDecisionResult(_Contract):
    review_id: str
    case_id: str
    case_state: str
    reason_code: str


class SimulationCreateRequest(_Contract):
    scenario: Literal["SUCCESS", "INSUFFICIENT_FUNDS", "ISSUER_OUTAGE", "TIMEOUT"]
    flow_type: Literal["ONE_TIME", "SUBSCRIPTION"]
    amount_minor: int = Field(gt=0, le=100_000_000)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")


class SimulationSessionView(_Contract):
    simulation_id: str
    merchant_display_name: str
    scenario: str
    flow_type: str
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: str
    classification: Literal["SYNTHETIC"]
    checkout_path: str
    expires_at: datetime


class SimulationEventItem(_Contract):
    event_id: str
    occurred_at: datetime
    category: Literal["INFO", "SUCCESS", "WARNING", "ERROR"]
    message: str


class SimulationEvents(_Contract):
    simulation_id: str
    status: str
    classification: Literal["SYNTHETIC"]
    events: tuple[SimulationEventItem, ...]


class SimulationAttemptResult(_Contract):
    simulation_id: str
    status: str
    classification: Literal["SYNTHETIC"]
    provider_event_id: str


class MerchantDashboardService(Protocol):
    async def business_overview(self, merchant_id: str, *, since: datetime) -> BusinessOverview: ...

    async def revenue_series(self, merchant_id: str, *, since: datetime) -> RevenueSeries: ...

    async def payments(
        self,
        merchant_id: str,
        *,
        statuses: tuple[str, ...],
        query: str | None,
        limit: int,
        offset: int,
    ) -> PaymentList: ...

    async def payment_detail(self, merchant_id: str, payment_id: str) -> PaymentDetail: ...

    async def recovery_overview(self, merchant_id: str) -> RecoveryOverview: ...

    async def incidents(self, merchant_id: str, *, active_only: bool) -> IncidentList: ...

    async def reviews(self, merchant_id: str) -> ReviewList: ...

    async def decide_review(
        self,
        merchant_id: str,
        review_id: str,
        *,
        operator_id: str,
        request: ReviewDecisionRequest,
    ) -> ReviewDecisionResult: ...

    async def create_simulation(
        self, merchant_id: str, request: SimulationCreateRequest
    ) -> SimulationSessionView: ...

    async def simulation(self, simulation_id: str) -> SimulationSessionView: ...

    async def submit_simulation(self, simulation_id: str) -> SimulationAttemptResult: ...

    async def simulation_events(self, merchant_id: str, simulation_id: str) -> SimulationEvents: ...


class UnavailableMerchantDashboardService:
    def __getattr__(self, _: str) -> object:
        async def unavailable(*args: object, **kwargs: object) -> object:
            del args, kwargs
            from revenueguard_api.dashboard import DashboardPersistenceError

            raise DashboardPersistenceError("merchant dashboard persistence is not configured")

        return unavailable
