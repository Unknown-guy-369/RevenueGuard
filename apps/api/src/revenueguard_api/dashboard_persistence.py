"""PostgreSQL-backed, tenant-scoped dashboard reads."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast

from revenueguard_integrations.persistence import (
    AsyncSessionFactory,
    CaseTransition,
    DecisionReceipt,
    EventDispatch,
    HumanReview,
    Merchant,
    ModelPrediction,
    RecoveryAction,
    RecoveryCase,
    SimulationSession,
    VerifiedOutcome,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from revenueguard_api.dashboard import (
    ActionItem,
    CaseDetail,
    CaseList,
    CaseSummary,
    CurrencyTotals,
    DashboardContext,
    DashboardCounts,
    DashboardNotFoundError,
    DashboardOverview,
    DashboardPersistenceError,
    DecisionItem,
    OperationsHealth,
    OutcomeItem,
    PredictionItem,
    ReviewItem,
    TransitionItem,
)

_TERMINAL_STATES = {"RECOVERED", "STOPPED"}


class DatabaseDashboardQueryService:
    """Derive dashboard state from PostgreSQL without mutating financial records."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def overview(self, merchant_id: str) -> DashboardOverview:
        try:
            async with self._session_factory() as session:
                context = await self._context(session, merchant_id)
                cases = tuple(
                    (
                        await session.scalars(
                            select(RecoveryCase)
                            .where(RecoveryCase.merchant_id == merchant_id)
                            .order_by(RecoveryCase.updated_at.desc(), RecoveryCase.id)
                        )
                    ).all()
                )
                outcomes = tuple(
                    (
                        await session.scalars(
                            select(VerifiedOutcome).where(
                                VerifiedOutcome.merchant_id == merchant_id,
                                VerifiedOutcome.outcome_status == "SUCCEEDED",
                                VerifiedOutcome.is_authoritative.is_(True),
                                VerifiedOutcome.recovered_amount_minor > 0,
                            )
                        )
                    ).all()
                )
                action_counts = await self._status_counts(
                    session, RecoveryAction, merchant_id, RecoveryAction.status
                )
                prediction_counts = await self._status_counts(
                    session, ModelPrediction, merchant_id, ModelPrediction.status
                )
                review_counts = await self._status_counts(
                    session, HumanReview, merchant_id, HumanReview.status
                )
                decision_count = await session.scalar(
                    select(func.count())
                    .select_from(DecisionReceipt)
                    .where(DecisionReceipt.merchant_id == merchant_id)
                )
                simulated_subjects = await self._simulation_subjects(session, merchant_id)
        except DashboardNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("dashboard overview query failed") from error

        at_risk: dict[str, int] = defaultdict(int)
        recovered: dict[str, int] = defaultdict(int)
        for case_row in cases:
            if case_row.state not in _TERMINAL_STATES:
                at_risk[case_row.currency] += case_row.revenue_at_risk_minor
        for outcome_row in outcomes:
            recovered[outcome_row.currency] += outcome_row.recovered_amount_minor
        currencies = sorted(set(at_risk) | set(recovered))
        state_counts: dict[str, int] = defaultdict(int)
        for case_row in cases:
            state_counts[case_row.state] += 1
        return DashboardOverview(
            context=context,
            currency_totals=tuple(
                CurrencyTotals(
                    currency=currency,
                    revenue_at_risk_minor=at_risk[currency],
                    verified_recovered_minor=recovered[currency],
                )
                for currency in currencies
            ),
            counts=DashboardCounts(
                active_cases=sum(case_row.state not in _TERMINAL_STATES for case_row in cases),
                recovered_cases=state_counts["RECOVERED"],
                stopped_cases=state_counts["STOPPED"],
                unknown_cases=state_counts["UNKNOWN"],
                deferred_cases=state_counts["DEFERRED"],
                escalated_cases=state_counts["ESCALATED"],
                pending_reviews=review_counts["REQUESTED"],
                pending_actions=action_counts["PENDING"],
                decision_receipts=decision_count or 0,
                model_succeeded=prediction_counts["SUCCEEDED"],
                model_fallback=prediction_counts["FALLBACK"],
            ),
            recent_cases=tuple(
                self._case_summary(item, synthetic=item.subject_id in simulated_subjects)
                for item in cases[:8]
            ),
        )

    async def list_cases(
        self,
        merchant_id: str,
        *,
        states: tuple[str, ...],
        limit: int,
    ) -> CaseList:
        try:
            async with self._session_factory() as session:
                context = await self._context(session, merchant_id)
                filters = [RecoveryCase.merchant_id == merchant_id]
                if states:
                    filters.append(RecoveryCase.state.in_(states))
                total = await session.scalar(
                    select(func.count()).select_from(RecoveryCase).where(*filters)
                )
                cases = tuple(
                    (
                        await session.scalars(
                            select(RecoveryCase)
                            .where(*filters)
                            .order_by(RecoveryCase.updated_at.desc(), RecoveryCase.id)
                            .limit(limit)
                        )
                    ).all()
                )
                simulated_subjects = await self._simulation_subjects(session, merchant_id)
        except DashboardNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("dashboard case list query failed") from error
        return CaseList(
            context=context,
            cases=tuple(
                self._case_summary(item, synthetic=item.subject_id in simulated_subjects)
                for item in cases
            ),
            total=total or 0,
        )

    async def case_detail(self, merchant_id: str, case_id: str) -> CaseDetail:
        try:
            async with self._session_factory() as session:
                context = await self._context(session, merchant_id)
                recovery_case = await session.scalar(
                    select(RecoveryCase).where(
                        RecoveryCase.merchant_id == merchant_id,
                        RecoveryCase.id == case_id,
                    )
                )
                if recovery_case is None:
                    raise DashboardNotFoundError("recovery case was not found")
                simulated_subjects = await self._simulation_subjects(session, merchant_id)
                transitions = tuple(
                    (
                        await session.scalars(
                            select(CaseTransition)
                            .where(
                                CaseTransition.merchant_id == merchant_id,
                                CaseTransition.recovery_case_id == case_id,
                            )
                            .order_by(CaseTransition.after_version, CaseTransition.id)
                        )
                    ).all()
                )
                decisions = tuple(
                    (
                        await session.scalars(
                            select(DecisionReceipt)
                            .where(
                                DecisionReceipt.merchant_id == merchant_id,
                                DecisionReceipt.recovery_case_id == case_id,
                            )
                            .order_by(DecisionReceipt.created_at, DecisionReceipt.id)
                        )
                    ).all()
                )
                predictions = tuple(
                    (
                        await session.scalars(
                            select(ModelPrediction)
                            .where(
                                ModelPrediction.merchant_id == merchant_id,
                                ModelPrediction.recovery_case_id == case_id,
                            )
                            .order_by(ModelPrediction.created_at, ModelPrediction.id)
                        )
                    ).all()
                )
                actions = tuple(
                    (
                        await session.scalars(
                            select(RecoveryAction)
                            .where(
                                RecoveryAction.merchant_id == merchant_id,
                                RecoveryAction.recovery_case_id == case_id,
                            )
                            .order_by(RecoveryAction.authorized_at, RecoveryAction.id)
                        )
                    ).all()
                )
                outcomes = tuple(
                    (
                        await session.scalars(
                            select(VerifiedOutcome)
                            .where(
                                VerifiedOutcome.merchant_id == merchant_id,
                                VerifiedOutcome.recovery_case_id == case_id,
                            )
                            .order_by(VerifiedOutcome.observed_at, VerifiedOutcome.id)
                        )
                    ).all()
                )
                reviews = tuple(
                    (
                        await session.scalars(
                            select(HumanReview)
                            .where(
                                HumanReview.merchant_id == merchant_id,
                                HumanReview.recovery_case_id == case_id,
                            )
                            .order_by(HumanReview.requested_at, HumanReview.id)
                        )
                    ).all()
                )
        except DashboardNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("dashboard case detail query failed") from error
        return CaseDetail(
            context=context,
            case=self._case_summary(
                recovery_case, synthetic=recovery_case.subject_id in simulated_subjects
            ),
            transitions=tuple(
                TransitionItem(
                    transition_id=str(item.id),
                    from_state=item.before_state,
                    to_state=item.after_state,
                    reason_code=item.reason_code,
                    reason_detail=item.reason_detail,
                    actor_reference_masked=_masked_reference("ACTOR", item.actor),
                    correlation_id=item.correlation_id,
                    policy_version=item.policy_version,
                    authoritative_evidence_reference=item.authoritative_evidence_reference,
                    occurred_at=item.occurred_at,
                    case_version=item.after_version,
                )
                for item in transitions
            ),
            decisions=tuple(
                DecisionItem(
                    decision_id=item.id,
                    selected_action_type=item.selected_action_type,
                    explanation=item.explanation,
                    policy_result=item.policy_result,
                    policy_reason_codes=tuple(item.policy_reason_codes),
                    policy_version=item.policy_version,
                    resulting_state=item.resulting_state,
                    resulting_action_id=item.resulting_action_id,
                    model_prediction_ids=tuple(item.model_prediction_ids),
                    created_at=item.created_at,
                )
                for item in decisions
            ),
            predictions=tuple(
                PredictionItem(
                    prediction_id=item.id,
                    node=item.node,
                    status=cast(Literal["SUCCEEDED", "FALLBACK"], item.status),
                    model_version=item.model_version,
                    prompt_version=item.prompt_version,
                    latency_ms=item.latency_ms,
                    input_tokens=item.input_tokens,
                    output_tokens=item.output_tokens,
                    failure_code=item.failure_code,
                    created_at=item.created_at,
                )
                for item in predictions
            ),
            actions=tuple(
                ActionItem(
                    action_id=item.id,
                    action_type=item.action_type,
                    target_reference_masked=_masked_reference(item.target_type, item.target_id),
                    logical_attempt=item.logical_attempt,
                    idempotency_key=item.idempotency_key,
                    status=item.status,
                    attempt_count=item.attempt_count,
                    max_attempts=item.max_attempts,
                    policy_version=item.policy_version,
                    authorized_at=item.authorized_at,
                    unknown_since=item.unknown_since,
                    last_error_code=item.last_error_code,
                )
                for item in actions
            ),
            outcomes=tuple(
                OutcomeItem(
                    outcome_id=item.id,
                    action_id=item.recovery_action_id,
                    status=item.outcome_status,
                    is_authoritative=item.is_authoritative,
                    recovered_amount_minor=item.recovered_amount_minor,
                    currency=item.currency,
                    evidence_source=item.evidence_source,
                    evidence_reference=item.evidence_reference,
                    provider_reference_masked=(
                        _masked_reference("PROVIDER", item.provider_object_id)
                        if item.provider_object_id
                        else None
                    ),
                    reason_code=item.reason_code,
                    observed_at=item.observed_at,
                    verified_at=item.verified_at,
                )
                for item in outcomes
            ),
            reviews=tuple(
                ReviewItem(
                    review_id=item.id,
                    status=item.status,
                    proposed_action_type=item.proposed_action_type,
                    reason_code=item.reason_code,
                    risk_detail=item.risk_detail,
                    policy_version=item.policy_version,
                    requested_at=item.requested_at,
                    expires_at=item.expires_at,
                    reviewed_at=item.decided_at,
                    reviewer_reference_masked=(
                        _masked_reference("REVIEWER", item.reviewer_id)
                        if item.reviewer_id
                        else None
                    ),
                    rationale=item.rationale,
                )
                for item in reviews
            ),
        )

    async def operations_health(self, merchant_id: str) -> OperationsHealth:
        try:
            async with self._session_factory() as session:
                context = await self._context(session, merchant_id)
                event_counts = await self._status_counts(
                    session, EventDispatch, merchant_id, EventDispatch.state
                )
                action_counts = await self._status_counts(
                    session, RecoveryAction, merchant_id, RecoveryAction.status
                )
        except DashboardNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise DashboardPersistenceError("dashboard health query failed") from error
        dead_letters = event_counts["DEAD_LETTER"]
        unknown_actions = action_counts["UNKNOWN"]
        return OperationsHealth(
            context=context,
            status="DEGRADED" if dead_letters or unknown_actions else "HEALTHY",
            pending_events=sum(
                event_counts[state]
                for state in ("PENDING", "PROCESSING", "PUBLISHED", "RETRY_SCHEDULED")
            ),
            dead_letter_events=dead_letters,
            pending_actions=action_counts["PENDING"],
            unknown_actions=unknown_actions,
        )

    @staticmethod
    async def _context(session: AsyncSession, merchant_id: str) -> DashboardContext:
        merchant = await session.scalar(
            select(Merchant).where(Merchant.id == merchant_id, Merchant.status == "ACTIVE")
        )
        if merchant is None:
            raise DashboardNotFoundError("merchant was not found")
        return DashboardContext(
            merchant_id=merchant.id,
            merchant_display_name=merchant.display_name,
            as_of=datetime.now(UTC),
        )

    @staticmethod
    async def _status_counts(
        session: AsyncSession,
        model: type[RecoveryAction]
        | type[ModelPrediction]
        | type[EventDispatch]
        | type[HumanReview],
        merchant_id: str,
        status_column: InstrumentedAttribute[str],
    ) -> dict[str, int]:
        rows = await session.execute(
            select(status_column, func.count())
            .where(model.merchant_id == merchant_id)
            .group_by(status_column)
        )
        return defaultdict(int, {str(status): int(count) for status, count in rows.all()})

    @staticmethod
    async def _simulation_subjects(session: AsyncSession, merchant_id: str) -> set[str]:
        rows = (
            await session.execute(
                select(SimulationSession.payment_id, SimulationSession.subscription_id).where(
                    SimulationSession.merchant_id == merchant_id
                )
            )
        ).all()
        return {subject for row in rows for subject in row if subject is not None}

    @staticmethod
    def _case_summary(item: RecoveryCase, *, synthetic: bool = False) -> CaseSummary:
        return CaseSummary(
            case_id=item.id,
            state=item.state,
            state_version=item.state_version,
            workflow_type=item.workflow_type,
            subject_type=item.subject_type,
            subject_reference_masked=_masked_reference(item.subject_type, item.subject_id),
            customer_reference_masked=(
                _masked_reference("CUSTOMER", item.customer_id) if item.customer_id else None
            ),
            revenue_at_risk_minor=item.revenue_at_risk_minor,
            currency=item.currency,
            diagnosis=item.diagnosis,
            diagnosis_confidence_basis_points=item.diagnosis_confidence_basis_points,
            retry_count=item.retry_count,
            contact_count=item.contact_count,
            classification="SYNTHETIC" if synthetic else "TEST",
            updated_at=item.updated_at,
        )


def _masked_reference(kind: str, value: str) -> str:
    digest = sha256(f"{kind}\0{value}".encode()).hexdigest()[:10].upper()
    return f"{kind} · {digest}"
