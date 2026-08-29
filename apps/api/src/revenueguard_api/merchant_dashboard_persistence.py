"""PostgreSQL-backed merchant reporting, approvals, and synthetic checkout sessions."""

from __future__ import annotations

import hmac
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Any, Literal, cast
from uuid import uuid4

from revenueguard_domain import HumanReviewDecision, ReviewDecisionType
from revenueguard_integrations.persistence import (
    AsyncSessionFactory,
    EventDispatch,
    EventIngestionRepository,
    HumanReview,
    IncidentCaseLink,
    Merchant,
    NormalizedEvent,
    Payment,
    PaymentOutcomeObservation,
    PortfolioIncident,
    RecoveryAction,
    RecoveryCase,
    RecoveryPersistenceError,
    RecoveryRepository,
    SimulationSession,
    VerifiedOutcome,
    WebhookEvent,
    session_scope,
)
from revenueguard_integrations.recovery import RecoveryApplicationService
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from revenueguard_api.dashboard import (
    DashboardContext,
    DashboardNotFoundError,
    DashboardPersistenceError,
)
from revenueguard_api.merchant_dashboard import (
    BusinessCurrencyTotals,
    BusinessOverview,
    IncidentList,
    IncidentSummary,
    MerchantDashboardConflictError,
    PaymentDetail,
    PaymentList,
    PaymentMethodShare,
    PaymentSummary,
    RecoveryCurrencyTotals,
    RecoveryOverview,
    RevenueSeries,
    RevenueSeriesPoint,
    ReviewDecisionRequest,
    ReviewDecisionResult,
    ReviewList,
    ReviewSummary,
    SimulationAttemptResult,
    SimulationCreateRequest,
    SimulationEventItem,
    SimulationEvents,
    SimulationSessionView,
)
from revenueguard_api.webhooks import verify_razorpay_signature

_SUCCESS_STATUSES = {"AUTHORIZED", "CAPTURED", "PAID", "SUCCESS", "SUCCEEDED"}
_FAILURE_STATUSES = {"FAILED", "DECLINED"}
_ACTIVE_CASE_STATES = {
    "DETECTED",
    "DIAGNOSING",
    "DECISION_PENDING",
    "POLICY_CHECK",
    "READY",
    "EXECUTING",
    "VERIFYING",
    "UNKNOWN",
    "DEFERRED",
    "ESCALATED",
}
_GENERATOR_VERSION = "dashboard-simulator-1.0"


class DatabaseMerchantDashboardService:
    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        simulator_secret: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: token_urlsafe(24))
        self._simulator_secret = simulator_secret or token_urlsafe(48)

    async def business_overview(self, merchant_id: str, *, since: datetime) -> BusinessOverview:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                payments = await _production_payments(session, merchant_id, since=since)
                recovered = await _production_recovered(session, merchant_id, since=since)
                simulated_payments = select(SimulationSession.payment_id).where(
                    SimulationSession.merchant_id == merchant_id
                )
                method_rows = (
                    await session.scalars(
                        select(PaymentOutcomeObservation).where(
                            PaymentOutcomeObservation.merchant_id == merchant_id,
                            PaymentOutcomeObservation.occurred_at >= since,
                            PaymentOutcomeObservation.payment_id.not_in(simulated_payments),
                        )
                    )
                ).all()
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("business overview query failed") from error

        by_currency: dict[str, dict[str, int]] = defaultdict(
            lambda: {"gross": 0, "collected": 0, "failed": 0, "count": 0, "ok": 0, "bad": 0}
        )
        for payment in payments:
            totals = by_currency[payment.currency]
            totals["gross"] += payment.amount_minor
            totals["count"] += 1
            status = payment.status.upper()
            if status in _SUCCESS_STATUSES:
                totals["collected"] += payment.amount_minor
                totals["ok"] += 1
            elif status in _FAILURE_STATUSES:
                totals["failed"] += payment.amount_minor
                totals["bad"] += 1
        recovered_by_currency = defaultdict(int, recovered)
        currencies = sorted(set(by_currency) | set(recovered_by_currency))
        currency_totals = tuple(
            BusinessCurrencyTotals(
                currency=currency,
                gross_volume_minor=by_currency[currency]["gross"],
                collected_minor=by_currency[currency]["collected"],
                failed_value_minor=by_currency[currency]["failed"],
                verified_recovered_minor=recovered_by_currency[currency],
                payment_count=by_currency[currency]["count"],
                successful_payment_count=by_currency[currency]["ok"],
                failed_payment_count=by_currency[currency]["bad"],
                success_rate_basis_points=_basis_points(
                    by_currency[currency]["ok"], by_currency[currency]["count"]
                ),
            )
            for currency in currencies
        )
        method_counts = Counter(row.payment_method for row in method_rows)
        method_total = sum(method_counts.values())
        return BusinessOverview(
            context=context,
            since=since,
            currency_totals=currency_totals,
            payment_methods=tuple(
                PaymentMethodShare(
                    payment_method=method,
                    payment_count=count,
                    share_basis_points=_basis_points(count, method_total),
                )
                for method, count in sorted(
                    method_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ),
        )

    async def revenue_series(self, merchant_id: str, *, since: datetime) -> RevenueSeries:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                payments = await _production_payments(session, merchant_id, since=since)
                recovered_rows = await _production_recovered_by_day(
                    session, merchant_id, since=since
                )
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("revenue series query failed") from error

        series: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"collected": 0, "failed": 0, "recovered": 0}
        )
        for payment in payments:
            key = (
                payment.provider_occurred_at.astimezone(UTC).date().isoformat(),
                payment.currency,
            )
            status = payment.status.upper()
            if status in _SUCCESS_STATUSES:
                series[key]["collected"] += payment.amount_minor
            elif status in _FAILURE_STATUSES:
                series[key]["failed"] += payment.amount_minor
        for occurred_on, currency, amount in recovered_rows:
            series[(occurred_on, currency)]["recovered"] += amount
        return RevenueSeries(
            context=context,
            since=since,
            points=tuple(
                RevenueSeriesPoint(
                    occurred_on=day,
                    currency=currency,
                    collected_minor=values["collected"],
                    failed_minor=values["failed"],
                    verified_recovered_minor=values["recovered"],
                )
                for (day, currency), values in sorted(series.items())
            ),
        )

    async def payments(
        self,
        merchant_id: str,
        *,
        statuses: tuple[str, ...],
        query: str | None,
        limit: int,
        offset: int,
    ) -> PaymentList:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                filters: list[Any] = [Payment.merchant_id == merchant_id]
                if statuses:
                    filters.append(func.upper(Payment.status).in_(statuses))
                if query:
                    pattern = f"%{_escape_like(query)}%"
                    filters.append(
                        or_(
                            Payment.id.ilike(pattern, escape="\\"),
                            Payment.provider_payment_id.ilike(pattern, escape="\\"),
                            Payment.customer_id.ilike(pattern, escape="\\"),
                        )
                    )
                total = int(
                    await session.scalar(select(func.count()).select_from(Payment).where(*filters))
                    or 0
                )
                rows = (
                    await session.scalars(
                        select(Payment)
                        .where(*filters)
                        .order_by(Payment.provider_occurred_at.desc(), Payment.id)
                        .limit(limit)
                        .offset(offset)
                    )
                ).all()
                summaries = await _payment_summaries(session, merchant_id, rows)
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("payment list query failed") from error
        return PaymentList(
            context=context,
            payments=summaries,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def payment_detail(self, merchant_id: str, payment_id: str) -> PaymentDetail:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                payment = await session.scalar(
                    select(Payment).where(
                        Payment.merchant_id == merchant_id, Payment.id == payment_id
                    )
                )
                if payment is None:
                    raise DashboardNotFoundError("payment was not found")
                summary = (await _payment_summaries(session, merchant_id, [payment]))[0]
                recovery_case = await _payment_case(session, merchant_id, payment.id)
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("payment detail query failed") from error
        return PaymentDetail(
            context=context,
            payment=summary,
            order_reference_masked=(
                _masked_reference("ORDER", payment.order_id) if payment.order_id else None
            ),
            diagnosis=recovery_case.diagnosis if recovery_case else None,
            next_evaluation_at=recovery_case.next_evaluation_at if recovery_case else None,
            updated_at=payment.updated_at,
        )

    async def recovery_overview(self, merchant_id: str) -> RecoveryOverview:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                cases = await _production_cases(session, merchant_id)
                recovered = await _production_recovered(session, merchant_id, since=None)
                pending_reviews = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(HumanReview)
                        .where(
                            HumanReview.merchant_id == merchant_id,
                            HumanReview.status == "REQUESTED",
                        )
                    )
                    or 0
                )
                active_incidents = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(PortfolioIncident)
                        .where(
                            PortfolioIncident.merchant_id == merchant_id,
                            PortfolioIncident.status == "ACTIVE",
                        )
                    )
                    or 0
                )
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("recovery overview query failed") from error
        risk: defaultdict[str, int] = defaultdict(int)
        for item in cases:
            if item.state in _ACTIVE_CASE_STATES:
                risk[item.currency] += item.revenue_at_risk_minor
        recovered_map = defaultdict(int, recovered)
        currencies = sorted(set(risk) | set(recovered_map))
        return RecoveryOverview(
            context=context,
            currency_totals=tuple(
                RecoveryCurrencyTotals(
                    currency=currency,
                    revenue_at_risk_minor=risk[currency],
                    verified_gross_recovered_minor=recovered_map[currency],
                )
                for currency in currencies
            ),
            active_cases=sum(item.state in _ACTIVE_CASE_STATES for item in cases),
            deferred_cases=sum(item.state == "DEFERRED" for item in cases),
            unknown_cases=sum(item.state == "UNKNOWN" for item in cases),
            pending_reviews=pending_reviews,
            active_incidents=active_incidents,
        )

    async def incidents(self, merchant_id: str, *, active_only: bool) -> IncidentList:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                statement = select(PortfolioIncident).where(
                    PortfolioIncident.merchant_id == merchant_id
                )
                if active_only:
                    statement = statement.where(PortfolioIncident.status == "ACTIVE")
                rows = (
                    await session.scalars(statement.order_by(PortfolioIncident.starts_at.desc()))
                ).all()
                paused_rows = (
                    await session.execute(
                        select(IncidentCaseLink.incident_id, func.count())
                        .where(IncidentCaseLink.merchant_id == merchant_id)
                        .group_by(IncidentCaseLink.incident_id)
                    )
                ).all()
                paused_counts: dict[str, int] = {
                    str(incident_id): int(count) for incident_id, count in paused_rows
                }
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("incident list query failed") from error
        incidents = tuple(
            IncidentSummary(
                incident_id=row.id,
                status=row.status,
                payment_method=row.payment_method,
                issuer_family=row.issuer_family,
                error_family=row.error_family,
                baseline_failure_rate_basis_points=row.baseline_failure_rate_basis_points,
                current_failure_rate_basis_points=row.current_failure_rate_basis_points,
                affected_payments=row.current_total,
                paused_cases=int(paused_counts.get(row.id, 0)),
                healthy_windows=row.clear_window_count,
                threshold_version=row.threshold_version,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                resolved_at=row.resolved_at,
            )
            for row in rows
        )
        return IncidentList(context=context, incidents=incidents, total=len(incidents))

    async def reviews(self, merchant_id: str) -> ReviewList:
        try:
            async with self._session_factory() as session:
                context = await _context(session, merchant_id, now=self._now())
                rows = (
                    await session.execute(
                        select(HumanReview, RecoveryCase)
                        .join(
                            RecoveryCase,
                            (RecoveryCase.merchant_id == HumanReview.merchant_id)
                            & (RecoveryCase.id == HumanReview.recovery_case_id),
                        )
                        .where(
                            HumanReview.merchant_id == merchant_id,
                            HumanReview.status == "REQUESTED",
                        )
                        .order_by(HumanReview.expires_at, HumanReview.id)
                    )
                ).all()
                simulated_subjects = {
                    subject
                    for simulation in (
                        await session.scalars(
                            select(SimulationSession).where(
                                SimulationSession.merchant_id == merchant_id
                            )
                        )
                    ).all()
                    for subject in (simulation.payment_id, simulation.subscription_id)
                    if subject is not None
                }
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("review queue query failed") from error
        reviews = tuple(
            ReviewSummary(
                review_id=review.id,
                case_id=case.id,
                customer_reference_masked=(
                    _masked_reference("CUSTOMER", case.customer_id) if case.customer_id else None
                ),
                amount_minor=case.revenue_at_risk_minor,
                currency=case.currency,
                proposed_action_type=review.proposed_action_type,
                diagnosis=case.diagnosis,
                confidence_basis_points=case.diagnosis_confidence_basis_points,
                reason_code=review.reason_code,
                risk_detail=review.risk_detail,
                policy_version=review.policy_version,
                classification=("SYNTHETIC" if case.subject_id in simulated_subjects else "TEST"),
                requested_at=review.requested_at,
                expires_at=review.expires_at,
            )
            for review, case in rows
        )
        return ReviewList(context=context, reviews=reviews, total=len(reviews))

    async def decide_review(
        self,
        merchant_id: str,
        review_id: str,
        *,
        operator_id: str,
        request: ReviewDecisionRequest,
    ) -> ReviewDecisionResult:
        decision_type = (
            ReviewDecisionType.APPROVE
            if request.decision == "APPROVE"
            else ReviewDecisionType.REJECT
        )
        try:
            async with session_scope(self._session_factory) as session:
                result = await RecoveryApplicationService(
                    RecoveryRepository(session), clock=self._clock
                ).decide_review(
                    merchant_id=merchant_id,
                    decision=HumanReviewDecision(
                        review_id=review_id,
                        decision=decision_type,
                        reviewer_id=operator_id,
                        rationale=request.rationale.strip(),
                        decided_at=self._now(),
                    ),
                )
        except LookupError as error:
            raise DashboardNotFoundError("human review was not found") from error
        except RecoveryPersistenceError as error:
            raise MerchantDashboardConflictError(str(error)) from error
        except ValueError as error:
            raise MerchantDashboardConflictError(str(error)) from error
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("review decision failed") from error
        if result.case_id is None or result.case_state is None:
            raise DashboardPersistenceError("review decision returned incomplete case state")
        return ReviewDecisionResult(
            review_id=review_id,
            case_id=result.case_id,
            case_state=result.case_state.value,
            reason_code=result.reason_code,
        )

    async def create_simulation(
        self, merchant_id: str, request: SimulationCreateRequest
    ) -> SimulationSessionView:
        now = self._now()
        token = self._token_factory()
        simulation_id = f"sim_{token}"
        digest = sha256(simulation_id.encode()).hexdigest()[:24]
        session_row = SimulationSession(
            merchant_id=merchant_id,
            id=simulation_id,
            scenario=request.scenario,
            flow_type=request.flow_type,
            amount_minor=request.amount_minor,
            currency=request.currency,
            customer_id=f"customer_sim_{digest}",
            payment_id=f"pay_sim_{digest}",
            subscription_id=(f"sub_sim_{digest}" if request.flow_type == "SUBSCRIPTION" else None),
            provider_event_id=None,
            status="CREATED",
            classification="SYNTHETIC",
            generator_version=_GENERATOR_VERSION,
            expires_at=now + timedelta(hours=1),
            attempted_at=None,
            created_at=now,
            updated_at=now,
        )
        try:
            async with session_scope(self._session_factory) as session:
                context = await _context(session, merchant_id, now=now)
                session.add(session_row)
                await session.flush()
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("simulation session creation failed") from error
        return _simulation_view(session_row, context.merchant_display_name)

    async def simulation(self, simulation_id: str) -> SimulationSessionView:
        try:
            async with self._session_factory() as session:
                row = await _public_simulation(session, simulation_id)
                merchant_name = await session.scalar(
                    select(Merchant.display_name).where(Merchant.id == row.merchant_id)
                )
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("simulation lookup failed") from error
        if merchant_name is None:
            raise DashboardNotFoundError("simulation merchant was not found")
        return _simulation_view(row, str(merchant_name))

    async def submit_simulation(self, simulation_id: str) -> SimulationAttemptResult:
        now = self._now()
        expired = False
        provider_event_id = ""
        try:
            async with session_scope(self._session_factory) as session:
                row = await session.scalar(
                    select(SimulationSession)
                    .where(SimulationSession.id == simulation_id)
                    .with_for_update()
                )
                if row is None:
                    raise DashboardNotFoundError("simulation session was not found")
                if row.status == "SUBMITTED" and row.provider_event_id:
                    return SimulationAttemptResult(
                        simulation_id=row.id,
                        status=row.status,
                        classification="SYNTHETIC",
                        provider_event_id=row.provider_event_id,
                    )
                if now >= row.expires_at:
                    row.status = "EXPIRED"
                    row.updated_at = now
                    expired = True
                else:
                    provider_event_id = f"sim_evt_{sha256(row.id.encode()).hexdigest()[:32]}"
                    payload = _simulation_payload(row, occurred_at=now)
                    raw_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
                    signature = hmac.new(
                        self._simulator_secret.encode(), raw_body, sha256
                    ).hexdigest()
                    if not verify_razorpay_signature(raw_body, signature, self._simulator_secret):
                        raise AssertionError("synthetic webhook signature verification failed")
                    result = await EventIngestionRepository(session).record_webhook(
                        event_id=str(uuid4()),
                        merchant_id=row.merchant_id,
                        provider="SIMULATOR",
                        provider_event_id=provider_event_id,
                        event_type=str(payload["event"]),
                        entity_id=row.subscription_id or row.payment_id,
                        raw_body=raw_body,
                        raw_payload=payload,
                        occurred_at=now,
                        received_at=now,
                        correlation_id=f"corr_{sha256(provider_event_id.encode()).hexdigest()[:48]}",
                    )
                    row.provider_event_id = provider_event_id
                    row.status = "SUBMITTED"
                    row.attempted_at = now
                    row.updated_at = now
                    await session.flush()
                    if not result.created:
                        raise MerchantDashboardConflictError("simulation event already exists")
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("simulation submission failed") from error
        if expired:
            raise MerchantDashboardConflictError("simulation session has expired")
        return SimulationAttemptResult(
            simulation_id=simulation_id,
            status="SUBMITTED",
            classification="SYNTHETIC",
            provider_event_id=provider_event_id,
        )

    async def simulation_events(self, merchant_id: str, simulation_id: str) -> SimulationEvents:
        try:
            async with self._session_factory() as session:
                row = await session.scalar(
                    select(SimulationSession).where(
                        SimulationSession.merchant_id == merchant_id,
                        SimulationSession.id == simulation_id,
                    )
                )
                if row is None:
                    raise DashboardNotFoundError("simulation session was not found")
                webhook = (
                    await session.scalar(
                        select(WebhookEvent).where(
                            WebhookEvent.merchant_id == merchant_id,
                            WebhookEvent.provider == "SIMULATOR",
                            WebhookEvent.provider_event_id == row.provider_event_id,
                        )
                    )
                    if row.provider_event_id
                    else None
                )
                dispatch = (
                    await session.scalar(
                        select(EventDispatch).where(
                            EventDispatch.merchant_id == merchant_id,
                            EventDispatch.webhook_event_id == webhook.id,
                        )
                    )
                    if webhook
                    else None
                )
                normalized = (
                    await session.scalar(
                        select(NormalizedEvent).where(
                            NormalizedEvent.merchant_id == merchant_id,
                            NormalizedEvent.webhook_event_id == webhook.id,
                        )
                    )
                    if webhook
                    else None
                )
                recovery_case = await _simulation_case(session, row)
                action = (
                    await session.scalar(
                        select(RecoveryAction)
                        .where(
                            RecoveryAction.merchant_id == merchant_id,
                            RecoveryAction.recovery_case_id == recovery_case.id,
                        )
                        .order_by(RecoveryAction.created_at.desc())
                        .limit(1)
                    )
                    if recovery_case
                    else None
                )
                outcome = (
                    await session.scalar(
                        select(VerifiedOutcome)
                        .where(
                            VerifiedOutcome.merchant_id == merchant_id,
                            VerifiedOutcome.recovery_case_id == recovery_case.id,
                        )
                        .order_by(VerifiedOutcome.observed_at.desc())
                        .limit(1)
                    )
                    if recovery_case
                    else None
                )
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("simulation timeline query failed") from error
        events = _simulation_event_items(
            row, webhook, dispatch, normalized, recovery_case, action, outcome
        )
        status = "CREATED"
        if row.status == "EXPIRED":
            status = "EXPIRED"
        elif outcome and outcome.is_authoritative:
            status = "COMPLETED"
        elif normalized and row.scenario == "SUCCESS":
            status = "COMPLETED"
        elif dispatch and dispatch.state == "DEAD_LETTER":
            status = "FAILED"
        elif row.status == "SUBMITTED":
            status = "PROCESSING"
        return SimulationEvents(
            simulation_id=row.id,
            status=status,
            classification="SYNTHETIC",
            events=events,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware timestamp")
        return value.astimezone(UTC)


async def _context(session: AsyncSession, merchant_id: str, *, now: datetime) -> DashboardContext:
    merchant = await session.scalar(
        select(Merchant).where(Merchant.id == merchant_id, Merchant.status == "ACTIVE")
    )
    if merchant is None:
        raise DashboardNotFoundError("merchant was not found")
    return DashboardContext(
        merchant_id=merchant.id,
        merchant_display_name=merchant.display_name,
        as_of=now,
    )


async def _production_payments(
    session: AsyncSession, merchant_id: str, *, since: datetime
) -> list[Payment]:
    simulated = select(SimulationSession.payment_id).where(
        SimulationSession.merchant_id == merchant_id
    )
    return list(
        (
            await session.scalars(
                select(Payment).where(
                    Payment.merchant_id == merchant_id,
                    Payment.provider_occurred_at >= since,
                    Payment.id.not_in(simulated),
                )
            )
        ).all()
    )


async def _production_cases(session: AsyncSession, merchant_id: str) -> list[RecoveryCase]:
    simulated_subjects = (
        select(SimulationSession.payment_id)
        .where(SimulationSession.merchant_id == merchant_id)
        .union(
            select(SimulationSession.subscription_id).where(
                SimulationSession.merchant_id == merchant_id,
                SimulationSession.subscription_id.is_not(None),
            )
        )
    )
    return list(
        (
            await session.scalars(
                select(RecoveryCase).where(
                    RecoveryCase.merchant_id == merchant_id,
                    RecoveryCase.subject_id.not_in(simulated_subjects),
                )
            )
        ).all()
    )


async def _production_recovered(
    session: AsyncSession, merchant_id: str, *, since: datetime | None
) -> list[tuple[str, int]]:
    simulated_subjects = (
        select(SimulationSession.payment_id)
        .where(SimulationSession.merchant_id == merchant_id)
        .union(
            select(SimulationSession.subscription_id).where(
                SimulationSession.merchant_id == merchant_id,
                SimulationSession.subscription_id.is_not(None),
            )
        )
    )
    statement = (
        select(VerifiedOutcome.currency, func.sum(VerifiedOutcome.recovered_amount_minor))
        .join(
            RecoveryCase,
            (RecoveryCase.merchant_id == VerifiedOutcome.merchant_id)
            & (RecoveryCase.id == VerifiedOutcome.recovery_case_id),
        )
        .where(
            VerifiedOutcome.merchant_id == merchant_id,
            VerifiedOutcome.is_authoritative.is_(True),
            VerifiedOutcome.outcome_status == "SUCCEEDED",
            RecoveryCase.subject_id.not_in(simulated_subjects),
        )
        .group_by(VerifiedOutcome.currency)
    )
    if since is not None:
        statement = statement.where(VerifiedOutcome.verified_at >= since)
    return [
        (str(currency), int(amount or 0))
        for currency, amount in (await session.execute(statement)).all()
    ]


async def _production_recovered_by_day(
    session: AsyncSession, merchant_id: str, *, since: datetime
) -> list[tuple[str, str, int]]:
    simulated_subjects = (
        select(SimulationSession.payment_id)
        .where(SimulationSession.merchant_id == merchant_id)
        .union(
            select(SimulationSession.subscription_id).where(
                SimulationSession.merchant_id == merchant_id,
                SimulationSession.subscription_id.is_not(None),
            )
        )
    )
    day = func.date(VerifiedOutcome.verified_at)
    rows = (
        await session.execute(
            select(day, VerifiedOutcome.currency, func.sum(VerifiedOutcome.recovered_amount_minor))
            .join(
                RecoveryCase,
                (RecoveryCase.merchant_id == VerifiedOutcome.merchant_id)
                & (RecoveryCase.id == VerifiedOutcome.recovery_case_id),
            )
            .where(
                VerifiedOutcome.merchant_id == merchant_id,
                VerifiedOutcome.is_authoritative.is_(True),
                VerifiedOutcome.outcome_status == "SUCCEEDED",
                VerifiedOutcome.verified_at >= since,
                RecoveryCase.subject_id.not_in(simulated_subjects),
            )
            .group_by(day, VerifiedOutcome.currency)
        )
    ).all()
    return [
        (value.isoformat(), str(currency), int(amount or 0)) for value, currency, amount in rows
    ]


async def _payment_summaries(
    session: AsyncSession, merchant_id: str, payments: Sequence[Payment]
) -> tuple[PaymentSummary, ...]:
    payment_ids = [payment.id for payment in payments]
    if not payment_ids:
        return ()
    observations = (
        await session.scalars(
            select(PaymentOutcomeObservation)
            .where(
                PaymentOutcomeObservation.merchant_id == merchant_id,
                PaymentOutcomeObservation.payment_id.in_(payment_ids),
            )
            .order_by(PaymentOutcomeObservation.occurred_at.desc())
        )
    ).all()
    methods: dict[str, str] = {}
    for item in observations:
        methods.setdefault(item.payment_id, item.payment_method)
    events = (
        await session.scalars(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.merchant_id == merchant_id,
                NormalizedEvent.payment_id.in_(payment_ids),
            )
            .order_by(NormalizedEvent.occurred_at.desc())
        )
    ).all()
    failures: dict[str, str] = {}
    case_by_payment: dict[str, RecoveryCase] = {}
    for event in events:
        if event.payment_id:
            if event.normalized_failure_category != "NONE":
                failures.setdefault(event.payment_id, event.normalized_failure_category)
            recovery_case = await session.scalar(
                select(RecoveryCase).where(
                    RecoveryCase.merchant_id == merchant_id,
                    RecoveryCase.latest_evidence_event_id == event.id,
                )
            )
            if recovery_case is not None:
                case_by_payment.setdefault(event.payment_id, recovery_case)
    simulations = {
        row.payment_id
        for row in (
            await session.scalars(
                select(SimulationSession).where(
                    SimulationSession.merchant_id == merchant_id,
                    SimulationSession.payment_id.in_(payment_ids),
                )
            )
        ).all()
    }
    return tuple(
        PaymentSummary(
            payment_id=payment.id,
            provider_reference_masked=_masked_reference("PAYMENT", payment.provider_payment_id),
            customer_reference_masked=(
                _masked_reference("CUSTOMER", payment.customer_id) if payment.customer_id else None
            ),
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            status=payment.status,
            payment_method=methods.get(payment.id),
            failure_category=failures.get(payment.id),
            recovery_case_id=(
                case_by_payment[payment.id].id if payment.id in case_by_payment else None
            ),
            recovery_state=(
                case_by_payment[payment.id].state if payment.id in case_by_payment else None
            ),
            classification="SYNTHETIC" if payment.id in simulations else "TEST",
            occurred_at=payment.provider_occurred_at,
        )
        for payment in payments
    )


async def _payment_case(
    session: AsyncSession, merchant_id: str, payment_id: str
) -> RecoveryCase | None:
    event_id = await session.scalar(
        select(NormalizedEvent.id)
        .where(
            NormalizedEvent.merchant_id == merchant_id,
            NormalizedEvent.payment_id == payment_id,
        )
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(1)
    )
    if event_id is None:
        return None
    return cast(
        RecoveryCase | None,
        await session.scalar(
            select(RecoveryCase).where(
                RecoveryCase.merchant_id == merchant_id,
                RecoveryCase.latest_evidence_event_id == event_id,
            )
        ),
    )


async def _public_simulation(session: AsyncSession, simulation_id: str) -> SimulationSession:
    row = await session.scalar(
        select(SimulationSession).where(SimulationSession.id == simulation_id)
    )
    if row is None:
        raise DashboardNotFoundError("simulation session was not found")
    return row


async def _simulation_case(
    session: AsyncSession, simulation: SimulationSession
) -> RecoveryCase | None:
    subjects = [simulation.payment_id]
    if simulation.subscription_id:
        subjects.append(simulation.subscription_id)
    return cast(
        RecoveryCase | None,
        await session.scalar(
            select(RecoveryCase)
            .where(
                RecoveryCase.merchant_id == simulation.merchant_id,
                RecoveryCase.subject_id.in_(subjects),
            )
            .order_by(RecoveryCase.created_at.desc())
            .limit(1)
        ),
    )


def _simulation_view(row: SimulationSession, merchant_name: str) -> SimulationSessionView:
    return SimulationSessionView(
        simulation_id=row.id,
        merchant_display_name=merchant_name,
        scenario=row.scenario,
        flow_type=row.flow_type,
        amount_minor=row.amount_minor,
        currency=row.currency,
        status=row.status,
        classification="SYNTHETIC",
        checkout_path=f"/demo/checkout/{row.id}",
        expires_at=row.expires_at,
    )


def _simulation_payload(row: SimulationSession, *, occurred_at: datetime) -> dict[str, object]:
    succeeded = row.scenario == "SUCCESS"
    event_type = "payment.captured" if succeeded else "payment.failed"
    payment: dict[str, object] = {
        "id": row.payment_id,
        "entity": "payment",
        "amount": row.amount_minor,
        "currency": row.currency,
        "status": "captured" if succeeded else "failed",
        "customer_id": row.customer_id,
        "created_at": int(occurred_at.timestamp()),
    }
    if not succeeded:
        failure = {
            "INSUFFICIENT_FUNDS": (
                "BAD_REQUEST_ERROR",
                "insufficient_funds",
                "The synthetic account has insufficient funds.",
            ),
            "ISSUER_OUTAGE": (
                "SERVER_ERROR",
                "issuer_unavailable",
                "The synthetic issuer is temporarily unavailable.",
            ),
            "TIMEOUT": (
                "GATEWAY_ERROR",
                "gateway_timeout",
                "The synthetic payment gateway timed out.",
            ),
        }[row.scenario]
        payment.update(error_code=failure[0], error_reason=failure[1], error_description=failure[2])
    payload: dict[str, object] = {"payment": {"entity": payment}}
    contains = ["payment"]
    if row.flow_type == "SUBSCRIPTION":
        event_type = "subscription.charged" if succeeded else "subscription.pending"
        payload["subscription"] = {
            "entity": {
                "id": row.subscription_id,
                "entity": "subscription",
                "customer_id": row.customer_id,
                "status": "active" if succeeded else "pending",
                "created_at": int(occurred_at.timestamp()),
            }
        }
        contains.insert(0, "subscription")
    return {
        "entity": "event",
        "account_id": "acc_synthetic_revenueguard_demo",
        "event": event_type,
        "contains": contains,
        "payload": payload,
        "created_at": int(occurred_at.timestamp()),
        "notes": {
            "classification": "SYNTHETIC",
            "generator_version": row.generator_version,
            "simulation_id": row.id,
        },
    }


def _simulation_event_items(
    simulation: SimulationSession,
    webhook: WebhookEvent | None,
    dispatch: EventDispatch | None,
    normalized: NormalizedEvent | None,
    recovery_case: RecoveryCase | None,
    action: RecoveryAction | None,
    outcome: VerifiedOutcome | None,
) -> tuple[SimulationEventItem, ...]:
    events = [
        SimulationEventItem(
            event_id=f"{simulation.id}:created",
            occurred_at=simulation.created_at,
            category="INFO",
            message="Synthetic Test Mode checkout created.",
        )
    ]
    if simulation.attempted_at:
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:submitted",
                occurred_at=simulation.attempted_at,
                category="INFO",
                message="Customer submitted the synthetic payment.",
            )
        )
    if webhook:
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:webhook",
                occurred_at=webhook.received_at,
                category="SUCCESS",
                message="Signed synthetic webhook verified and stored durably.",
            )
        )
    if dispatch:
        category: Literal["INFO", "SUCCESS", "WARNING", "ERROR"] = (
            "ERROR" if dispatch.state == "DEAD_LETTER" else "INFO"
        )
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:dispatch:{dispatch.state}",
                occurred_at=(dispatch.completed_at or dispatch.published_at or dispatch.created_at),
                category=category,
                message=f"Event dispatch state: {dispatch.state.lower().replace('_', ' ')}.",
            )
        )
    if normalized:
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:normalized",
                occurred_at=normalized.created_at,
                category="SUCCESS",
                message=f"Payment normalized as {normalized.normalized_failure_category}.",
            )
        )
    if recovery_case:
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:case:{recovery_case.state_version}",
                occurred_at=recovery_case.updated_at,
                category="WARNING" if recovery_case.state in {"DEFERRED", "UNKNOWN"} else "INFO",
                message=f"Recovery case is {recovery_case.state.lower().replace('_', ' ')}.",
            )
        )
    if action:
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:action:{action.id}:{action.status}",
                occurred_at=action.updated_at,
                category="WARNING" if action.status == "UNKNOWN" else "INFO",
                message=(
                    f"{action.action_type.replace('_', ' ').title()} action: "
                    f"{action.status.lower()}."
                ),
            )
        )
    if outcome:
        events.append(
            SimulationEventItem(
                event_id=f"{simulation.id}:outcome:{outcome.id}",
                occurred_at=outcome.observed_at,
                category="SUCCESS" if outcome.is_authoritative else "WARNING",
                message=(
                    "Authoritative simulated outcome verified."
                    if outcome.is_authoritative
                    else "Outcome remains unverified."
                ),
            )
        )
    return tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id)))


def _masked_reference(kind: str, value: str) -> str:
    digest = sha256(f"{kind}\0{value}".encode()).hexdigest()[:10].upper()
    return f"{kind} · {digest}"


def _basis_points(numerator: int, denominator: int) -> int:
    return numerator * 10_000 // denominator if denominator else 0


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
