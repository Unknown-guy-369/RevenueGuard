"""Application services for receivables and payment-degradation playbooks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from revenueguard_domain import (
    ActionStatus,
    ActionType,
    CandidateAction,
    CaseState,
    ContactChannel,
    DecisionReceipt,
    DegradationAssessment,
    DegradationPolicy,
    PaymentOutcomeObservation,
    PolicyEvaluationInput,
    PolicyResult,
    PromiseIntent,
    RecoveryAction,
    SubjectType,
    VersionBundle,
    action_idempotency_key,
    create_promise,
    evaluate_policy,
    transition_case,
)
from revenueguard_domain import (
    RecoveryCase as DomainRecoveryCase,
)

from revenueguard_integrations.persistence.action_repositories import ActionRepository
from revenueguard_integrations.persistence.playbook_repositories import PlaybookRepository
from revenueguard_integrations.persistence.recovery_repositories import RecoveryRepository
from revenueguard_integrations.playbooks.extraction import BoundedPromiseExtractor
from revenueguard_integrations.recovery import RecoveryApplicationService, RecoveryServiceResult


@dataclass(frozen=True, slots=True)
class CustomerResponseResult:
    response_id: str
    case_id: str
    intent: PromiseIntent
    disposition: str
    promise_id: str | None = None
    escalation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PromiseMaintenanceResult:
    promise_id: str
    disposition: str
    action_id: str | None = None
    escalation_id: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioMaintenanceResult:
    merchants_evaluated: int
    assessments_applied: int
    incidents_resolved: int


class ReceivablesPlaybookService:
    def __init__(
        self,
        repository: PlaybookRepository,
        recovery_repository: RecoveryRepository,
        *,
        extractor: BoundedPromiseExtractor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        effective_clock = clock or (lambda: datetime.now(UTC))
        self._repository = repository
        self._recovery_repository = recovery_repository
        self._recovery = RecoveryApplicationService(
            recovery_repository,
            clock=effective_clock,
        )
        self._extractor = extractor or BoundedPromiseExtractor()
        self._clock = effective_clock
        self._actions = ActionRepository(recovery_repository.session)

    async def ingest_overdue_invoice(
        self,
        *,
        merchant_id: str,
        source_event_id: str,
        correlation_id: str,
        invoice_id: str,
        customer_id: str,
        amount_minor: int,
        outstanding_amount_minor: int,
        currency: str,
        due_at: datetime,
        occurred_at: datetime,
        received_at: datetime,
    ) -> RecoveryServiceResult:
        token = _stable_token(merchant_id, source_event_id)
        normalized = await self._repository.record_overdue_invoice(
            merchant_event_id=f"merchant_event_{token}",
            normalized_event_id=f"event_{token}",
            merchant_id=merchant_id,
            source_event_id=source_event_id,
            correlation_id=correlation_id,
            invoice_id=invoice_id,
            customer_id=customer_id,
            amount_minor=amount_minor,
            outstanding_amount_minor=outstanding_amount_minor,
            currency=currency,
            due_at=due_at,
            occurred_at=occurred_at,
            received_at=received_at,
        )
        return await self._recovery.process_event(
            merchant_id=merchant_id,
            normalized_event_id=normalized.id,
        )

    async def record_customer_response(
        self,
        *,
        merchant_id: str,
        source_response_id: str,
        invoice_id: str,
        body: str,
        received_at: datetime | None = None,
    ) -> CustomerResponseResult:
        at = _utc(received_at or self._clock())
        invoice = await self._repository.invoice(
            merchant_id=merchant_id,
            invoice_id=invoice_id,
            for_update=True,
        )
        if invoice is None:
            raise LookupError("tenant-scoped invoice does not exist")
        case = await self._recovery_repository.find_active_case(
            merchant_id=merchant_id,
            workflow_type="B2B_PROMISE_TO_PAY",
            subject_type="INVOICE",
            subject_id=invoice_id,
            for_update=True,
        )
        if case is None:
            raise LookupError("invoice has no active recovery case")
        extraction = await self._extractor.extract(body)
        response_token = _stable_token(merchant_id, source_response_id)
        response, created = await self._repository.store_customer_response(
            response_id=f"response_{response_token}",
            merchant_id=merchant_id,
            source_response_id=source_response_id,
            case_id=case.id,
            invoice_id=invoice_id,
            customer_id=invoice.customer_id,
            body=body,
            extraction=extraction,
            received_at=at,
        )
        if not created:
            return CustomerResponseResult(
                response.id,
                case.id,
                PromiseIntent(response.intent),
                "DUPLICATE_RESPONSE",
            )
        if extraction.intent is PromiseIntent.PROMISE_TO_PAY:
            promise = create_promise(
                promise_id=f"promise_{response_token}",
                merchant_id=merchant_id,
                case_id=case.id,
                invoice_id=invoice_id,
                customer_id=invoice.customer_id,
                outstanding_amount_minor=invoice.outstanding_amount_minor,
                invoice_currency=invoice.currency,
                extraction=extraction,
                source_response_id=source_response_id,
                received_at=at,
            )
            await self._repository.store_promise(promise, customer_response_id=response.id)
            return CustomerResponseResult(
                response.id,
                case.id,
                extraction.intent,
                "PROMISE_SCHEDULED",
                promise_id=promise.promise_id,
            )
        escalation_id = f"escalation_{response_token}"
        if extraction.intent is PromiseIntent.DISPUTE:
            await self._repository.freeze_dispute_and_escalate(
                escalation_id=escalation_id,
                merchant_id=merchant_id,
                case_id=case.id,
                invoice_id=invoice_id,
                customer_response_id=response.id,
                occurred_at=at,
            )
            disposition = "AUTOMATION_FROZEN_HUMAN_ESCALATION"
        elif extraction.intent is PromiseIntent.ALREADY_PAID:
            await self._repository.freeze_already_paid_claim_and_escalate(
                escalation_id=escalation_id,
                merchant_id=merchant_id,
                case_id=case.id,
                invoice_id=invoice_id,
                customer_response_id=response.id,
                occurred_at=at,
            )
            disposition = "AUTHORITATIVE_VERIFICATION_REQUIRED"
        else:
            await self._repository.store_receivable_escalation(
                escalation_id=escalation_id,
                merchant_id=merchant_id,
                case_id=case.id,
                invoice_id=invoice_id,
                customer_response_id=response.id,
                reason_code="CUSTOMER_RESPONSE_REQUIRES_REVIEW",
                occurred_at=at,
            )
            disposition = "HUMAN_REVIEW_REQUIRED"
        return CustomerResponseResult(
            response.id,
            case.id,
            extraction.intent,
            disposition,
            escalation_id=escalation_id,
        )

    async def schedule_due_promise_reminders(
        self, *, due_at: datetime, limit: int
    ) -> tuple[PromiseMaintenanceResult, ...]:
        evaluated_at = _utc(due_at)
        promises = await self._repository.due_promises(
            due_at=evaluated_at,
            limit=limit,
        )
        results: list[PromiseMaintenanceResult] = []
        for promise in promises:
            case = await self._recovery_repository.get_case(
                merchant_id=promise.merchant_id,
                case_id=promise.recovery_case_id,
                for_update=True,
            )
            if case is None:
                raise LookupError("promise recovery case does not exist")
            policy = await self._recovery_repository.effective_policy(
                merchant_id=promise.merchant_id,
                evaluated_at=evaluated_at,
            )
            correlation_id = f"promise:{promise.id}"
            if case.state in {CaseState.DEFERRED, CaseState.VERIFYING}:
                case = await self._persist_transition(
                    case=case,
                    to_state=CaseState.DECISION_PENDING,
                    reason_code="PROMISE_REMINDER_DUE",
                    correlation_id=correlation_id,
                    policy_version=policy.version,
                    occurred_at=evaluated_at,
                )
            if case.state is not CaseState.DECISION_PENDING:
                results.append(
                    PromiseMaintenanceResult(promise.id, "CASE_NOT_ELIGIBLE_FOR_REMINDER")
                )
                continue
            checking = await self._persist_transition(
                case=case,
                to_state=CaseState.POLICY_CHECK,
                reason_code="PROMISE_REMINDER_POLICY_CHECK",
                correlation_id=correlation_id,
                policy_version=policy.version,
                occurred_at=evaluated_at,
            )
            candidate = CandidateAction(
                action_type=ActionType.SCHEDULE_PROMISE_REMINDER,
                recovery_probability_basis_points=7_000,
                expected_net_recovery_minor=promise.amount_minor * 6_000 // 10_000,
                rank=1,
                target=promise.invoice_id,
                channel=ContactChannel.EMAIL,
            )
            no_action = CandidateAction(
                action_type=ActionType.NO_ACTION,
                recovery_probability_basis_points=0,
                expected_net_recovery_minor=0,
                rank=2,
                target=promise.invoice_id,
            )
            consent, opted_out = await self._recovery_repository.consent_facts(
                merchant_id=promise.merchant_id,
                customer_id=promise.customer_id,
            )
            incidents = await self._recovery_repository.active_incidents(
                merchant_id=promise.merchant_id,
                evaluated_at=evaluated_at,
                case_id=checking.case_id,
                diagnosis_code=checking.diagnosis,
            )
            decision = evaluate_policy(
                policy,
                PolicyEvaluationInput(
                    case_id=checking.case_id,
                    amount_minor=promise.amount_minor,
                    currency=promise.currency,
                    confidence_basis_points=promise.extraction_confidence_basis_points,
                    retry_count=checking.retry_count,
                    contact_count=checking.contact_count,
                    evaluated_at=evaluated_at,
                    candidates=(candidate, no_action),
                    evidence_references=(promise.customer_response_id,),
                    consent_by_channel=consent,
                    opted_out_channels=opted_out,
                    incidents=incidents,
                    active_promise_to_pay=True,
                    promise_due_at=promise.promised_for,
                ),
            )
            decided = await self._persist_transition(
                case=checking,
                to_state=decision.resulting_state,
                reason_code=decision.reason_codes[-1],
                correlation_id=correlation_id,
                policy_version=policy.version,
                occurred_at=evaluated_at,
                terminal_reason=(
                    decision.reason_codes[-1] if decision.result is PolicyResult.STOP else None
                ),
                next_evaluation_at=decision.next_evaluation_at,
            )
            receipt_token = _stable_token(
                promise.merchant_id,
                promise.id,
                evaluated_at.isoformat(),
            )
            receipt_id = f"receipt_{receipt_token}"
            action = None
            if decision.result is PolicyResult.PROCEED:
                key = action_idempotency_key(
                    merchant_id=promise.merchant_id,
                    case_id=case.case_id,
                    action_type=ActionType.SCHEDULE_PROMISE_REMINDER,
                    target_type=SubjectType.INVOICE,
                    target_id=promise.invoice_id,
                    logical_attempt=1,
                )
                action = RecoveryAction(
                    action_id=f"action_{key.rsplit(':', maxsplit=1)[-1][:32]}",
                    case_id=case.case_id,
                    merchant_id=promise.merchant_id,
                    decision_receipt_id=receipt_id,
                    action_type=ActionType.SCHEDULE_PROMISE_REMINDER,
                    target_type=SubjectType.INVOICE,
                    target_id=promise.invoice_id,
                    logical_attempt=1,
                    idempotency_key=key,
                    status=ActionStatus.PENDING,
                    parameters={
                        "amount_minor": promise.amount_minor,
                        "currency": promise.currency,
                        "provider_mode": "TEST",
                        "channel": "SIMULATED_EMAIL",
                        "promise_id": promise.id,
                    },
                    authorized_at=evaluated_at,
                    execute_after=evaluated_at,
                    created_at=evaluated_at,
                )
            receipt = DecisionReceipt(
                receipt_id=receipt_id,
                case_id=case.case_id,
                merchant_id=promise.merchant_id,
                correlation_id=correlation_id,
                evidence_references=(promise.customer_response_id,),
                candidate_actions=(candidate, no_action),
                selected_action_type=decision.selected_action.action_type,
                explanation="Deterministic promise reminder policy evaluation.",
                policy_result=decision.result,
                policy_reason_codes=decision.reason_codes,
                versions=VersionBundle(
                    policy=policy.version,
                    features="phase6-promise-reminder-1.0",
                    application="phase6-playbooks-1.0",
                ),
                created_at=evaluated_at,
                resulting_state=decided.state,
                resulting_action_id=action.action_id if action else None,
            )
            await self._recovery_repository.store_receipt(receipt)
            if action is not None:
                await self._actions.store_action(
                    action,
                    policy_version=policy.version,
                    correlation_id=correlation_id,
                    max_attempts=3,
                    reconciliation_deadline=evaluated_at + timedelta(hours=1),
                )
                await self._repository.mark_promise_reminder_scheduled(
                    merchant_id=promise.merchant_id,
                    promise_id=promise.id,
                    action_id=action.action_id,
                    scheduled_at=evaluated_at,
                )
                results.append(
                    PromiseMaintenanceResult(
                        promise.id,
                        "REMINDER_AUTHORIZED",
                        action_id=action.action_id,
                    )
                )
            else:
                if decision.next_evaluation_at is not None:
                    await self._repository.reschedule_promise_reminder(
                        merchant_id=promise.merchant_id,
                        promise_id=promise.id,
                        reminder_at=decision.next_evaluation_at,
                    )
                results.append(
                    PromiseMaintenanceResult(
                        promise.id,
                        decision.reason_codes[-1],
                    )
                )
        return tuple(results)

    async def escalate_broken_promises(
        self, *, due_at: datetime, limit: int
    ) -> tuple[PromiseMaintenanceResult, ...]:
        at = _utc(due_at)
        promises = await self._repository.due_promises(due_at=at, limit=limit, broken=True)
        results: list[PromiseMaintenanceResult] = []
        for promise in promises:
            invoice = await self._repository.invoice(
                merchant_id=promise.merchant_id,
                invoice_id=promise.invoice_id,
                for_update=True,
            )
            if invoice is None:
                raise LookupError("promise invoice does not exist")
            if invoice.status == "PAID":
                promise.status = "FULFILLED"
                promise.fulfilled_at = at
                promise.updated_at = at
                results.append(PromiseMaintenanceResult(promise.id, "PROMISE_FULFILLED"))
                continue
            escalation_id = f"escalation_{_stable_token(promise.merchant_id, promise.id, 'broken')}"
            await self._repository.mark_broken_promise_and_escalate(
                promise=promise,
                escalation_id=escalation_id,
                broken_at=at,
            )
            results.append(
                PromiseMaintenanceResult(
                    promise.id,
                    "BROKEN_PROMISE_ESCALATED",
                    escalation_id=escalation_id,
                )
            )
        return tuple(results)

    async def _persist_transition(
        self,
        *,
        case: DomainRecoveryCase,
        to_state: CaseState,
        reason_code: str,
        correlation_id: str,
        policy_version: str,
        occurred_at: datetime,
        terminal_reason: str | None = None,
        next_evaluation_at: datetime | None = None,
    ) -> DomainRecoveryCase:
        updated, transition = transition_case(
            case,
            expected_version=case.state_version,
            to_state=to_state,
            actor="RECEIVABLES_PLAYBOOK",
            reason_code=reason_code,
            correlation_id=correlation_id,
            policy_version=policy_version,
            occurred_at=occurred_at,
            terminal_reason=terminal_reason,
            next_evaluation_at=next_evaluation_at,
        )
        await self._recovery_repository.apply_transition(
            updated_case=updated,
            transition=transition,
        )
        return updated


class PaymentDegradationService:
    def __init__(
        self,
        repository: PlaybookRepository,
        *,
        policy: DegradationPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or DegradationPolicy()

    async def observe_and_evaluate(
        self,
        observation: PaymentOutcomeObservation,
        *,
        source_event_id: str,
        evaluated_at: datetime,
    ) -> tuple[DegradationAssessment, ...]:
        evaluated = _utc(evaluated_at)
        await self._repository.lock_merchant(merchant_id=observation.merchant_id)
        created = await self._repository.record_payment_observation(
            observation,
            source_event_id=source_event_id,
        )
        observations = await self._repository.payment_observations(
            merchant_id=observation.merchant_id,
            since=evaluated - self._policy.baseline_window,
            until=evaluated,
        )
        from revenueguard_domain import assess_payment_degradation

        assessments = assess_payment_degradation(
            observations,
            evaluated_at=evaluated,
            policy=self._policy,
        )
        if not created:
            return assessments
        for assessment in assessments:
            incident_token = _stable_token(
                assessment.merchant_id,
                assessment.payment_method,
                assessment.issuer_family,
                assessment.error_family,
                assessment.evaluated_at.isoformat(),
            )
            await self._repository.apply_degradation_assessment(
                assessment,
                policy=self._policy,
                incident_id=f"incident_{incident_token}",
            )
        return assessments

    async def maintain_portfolios(
        self, *, evaluated_at: datetime, merchant_limit: int
    ) -> PortfolioMaintenanceResult:
        evaluated = _utc(evaluated_at)
        merchant_ids = await self._repository.portfolio_merchant_ids(
            since=evaluated - self._policy.baseline_window,
            limit=merchant_limit,
        )
        applied = 0
        resolved = 0
        from revenueguard_domain import assess_payment_degradation

        for merchant_id in merchant_ids:
            await self._repository.lock_merchant(merchant_id=merchant_id)
            observations = await self._repository.payment_observations(
                merchant_id=merchant_id,
                since=evaluated - self._policy.baseline_window,
                until=evaluated,
            )
            assessments = list(
                assess_payment_degradation(
                    observations,
                    evaluated_at=evaluated,
                    policy=self._policy,
                )
            )
            assessed_dimensions = {
                (item.payment_method, item.issuer_family, item.error_family) for item in assessments
            }
            active_incidents = await self._repository.active_incidents_for_maintenance(
                merchant_id=merchant_id
            )
            for incident in active_incidents:
                dimension = (
                    incident.payment_method or "UNKNOWN",
                    incident.issuer_family or "UNKNOWN",
                    incident.error_family or "UNKNOWN",
                )
                if dimension in assessed_dimensions:
                    continue
                assessments.append(
                    DegradationAssessment(
                        merchant_id=merchant_id,
                        payment_method=dimension[0],
                        issuer_family=dimension[1],
                        error_family=dimension[2],
                        baseline_total=0,
                        baseline_failures=0,
                        current_total=0,
                        current_failures=0,
                        baseline_failure_rate_basis_points=0,
                        current_failure_rate_basis_points=0,
                        failure_rate_increase_basis_points=0,
                        failure_rate_ratio_basis_points=10_000,
                        degraded=False,
                        evaluated_at=evaluated,
                        policy_version=self._policy.version,
                    )
                )
            for assessment in assessments:
                incident_token = _stable_token(
                    assessment.merchant_id,
                    assessment.payment_method,
                    assessment.issuer_family,
                    assessment.error_family,
                    assessment.evaluated_at.isoformat(),
                )
                applied_incident = await self._repository.apply_degradation_assessment(
                    assessment,
                    policy=self._policy,
                    incident_id=f"incident_{incident_token}",
                )
                if applied_incident is not None:
                    applied += 1
                    if applied_incident.status == "RESOLVED":
                        resolved += 1
        return PortfolioMaintenanceResult(
            merchants_evaluated=len(merchant_ids),
            assessments_applied=applied,
            incidents_resolved=resolved,
        )


def _stable_token(*parts: str) -> str:
    return sha256(":".join(parts).encode()).hexdigest()[:32]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
