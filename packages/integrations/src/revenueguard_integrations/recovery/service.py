"""Transactional recovery decisioning and action authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Final
from uuid import uuid4

from revenueguard_agents import (
    BoundedCaseIntelligence,
    CaseIntelligence,
    CaseIntelligenceRequest,
    EvidenceItem,
)
from revenueguard_domain import (
    ACTION_CLASSES,
    ActionClass,
    ActionEconomics,
    ActionFingerprintInput,
    ActionStatus,
    ActionType,
    CandidateAction,
    CaseState,
    CustomerIntervention,
    CustomerInterventionStatus,
    DecisionReceipt,
    Diagnosis,
    EventSource,
    HumanReviewDecision,
    HumanReviewRequest,
    IncidentScope,
    LogisticScoringArtifact,
    MerchantPolicySnapshot,
    NormalizedFailureCategory,
    PolicyDecision,
    PolicyEvaluationInput,
    PolicyResult,
    RecoveryAction,
    RecoveryCase,
    RecoveryScoringContext,
    RecoveryScoringResult,
    RevenueRiskEvent,
    ReviewDecisionType,
    ReviewStatus,
    VersionBundle,
    action_idempotency_key,
    default_action_economics,
    diagnose_event,
    evaluate_policy,
    expire_review,
    rank_candidates_by_expected_net_recovery,
    select_case_identity,
    synthetic_default_scoring_artifact,
    transition_case,
)
from revenueguard_domain import (
    decide_review as apply_review_decision,
)

from revenueguard_integrations.persistence import (
    ActionRepository,
    EvidenceDisposition,
    NormalizedEvent,
    RecoveryRepository,
    order_evidence,
)

APPLICATION_VERSION: Final = "0.1.0-phase7"
_PAID_STATUSES: Final = frozenset({"CAPTURED", "CHARGED", "COMPLETED", "PAID"})
_CANCELLED_STATUSES: Final = frozenset({"CANCELLED", "ESCALATED"})

Clock = Callable[[], datetime]
IdGenerator = Callable[[str], str]


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryServiceResult:
    normalized_event_id: str | None
    case_id: str | None
    case_state: CaseState | None
    disposition: EvidenceDisposition | None
    reason_code: str
    receipt_id: str | None = None
    review_id: str | None = None
    action_id: str | None = None


class RecoveryApplicationService:
    """Authorize durable actions without ever calling an external provider."""

    def __init__(
        self,
        repository: RecoveryRepository,
        *,
        action_repository: ActionRepository | None = None,
        case_intelligence: CaseIntelligence | None = None,
        clock: Clock | None = None,
        id_generator: IdGenerator | None = None,
        application_version: str = APPLICATION_VERSION,
        review_ttl: timedelta = timedelta(hours=24),
        scoring_artifact: LogisticScoringArtifact | None = None,
        action_economics: ActionEconomics | None = None,
    ) -> None:
        if not application_version:
            raise ValueError("application_version is required")
        if review_ttl <= timedelta(0):
            raise ValueError("review_ttl must be positive")
        self._repository = repository
        self._action_repository = action_repository
        if self._action_repository is None:
            repository_session = getattr(repository, "session", None)
            if repository_session is not None:
                self._action_repository = ActionRepository(repository_session)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_generator = id_generator or _new_id
        self._case_intelligence = case_intelligence or BoundedCaseIntelligence()
        self._application_version = application_version
        self._review_ttl = review_ttl
        self._scoring_artifact = scoring_artifact or synthetic_default_scoring_artifact()
        self._action_economics = action_economics or default_action_economics()

    async def process_event(
        self, *, merchant_id: str, normalized_event_id: str
    ) -> RecoveryServiceResult:
        evaluated_at = _utc(self._clock())
        await self._repository.lock_merchant(merchant_id=merchant_id)
        event_row = await self._repository.get_normalized_event(
            merchant_id=merchant_id,
            normalized_event_id=normalized_event_id,
        )
        if event_row is None:
            raise LookupError("tenant-scoped normalized event does not exist")
        event = _event_from_row(event_row)
        identity = select_case_identity(event)
        if identity is None:
            link = await self._repository.link_evidence(
                merchant_id=merchant_id,
                normalized_event_id=normalized_event_id,
                recovery_case_id=None,
                disposition=EvidenceDisposition.AUDIT_ONLY,
                reason_code="UNSUPPORTED_EVENT_SUBJECT",
            )
            return RecoveryServiceResult(
                normalized_event_id=normalized_event_id,
                case_id=None,
                case_state=None,
                disposition=EvidenceDisposition(link.link.disposition),
                reason_code=(
                    "UNSUPPORTED_EVENT_SUBJECT" if link.created else "EVENT_ALREADY_LINKED"
                ),
            )

        diagnosis = diagnose_event(event)
        case_row = await self._repository.find_active_case(
            merchant_id=merchant_id,
            workflow_type=identity.workflow_type.value,
            subject_type=identity.subject_type.value,
            subject_id=identity.subject_id,
            for_update=True,
        )
        if case_row is None and identity.episode_key is not None:
            case_row = await self._repository.find_episode_case(
                merchant_id=merchant_id,
                workflow_type=identity.workflow_type.value,
                subject_type=identity.subject_type.value,
                recovery_episode_key=identity.episode_key,
            )

        if diagnosis is None:
            return await self._link_audit_only(
                event_row=event_row,
                case_id=case_row.id if case_row else None,
                case_state=CaseState(case_row.state) if case_row else None,
                reason_code="EVENT_HAS_NO_RECOVERY_DIAGNOSIS",
            )
        if case_row is not None and CaseState(case_row.state) in {
            CaseState.RECOVERED,
            CaseState.STOPPED,
        }:
            return await self._link_audit_only(
                event_row=event_row,
                case_id=case_row.id,
                case_state=CaseState(case_row.state),
                reason_code="TERMINAL_CASE_EVIDENCE",
            )

        policy = await self._repository.effective_policy(
            merchant_id=merchant_id,
            evaluated_at=evaluated_at,
        )
        if case_row is None:
            case = RecoveryCase(
                case_id=self._id_generator("case"),
                merchant_id=merchant_id,
                workflow_type=identity.workflow_type,
                subject_type=identity.subject_type,
                subject_id=identity.subject_id,
                customer_id=event.customer_id,
                revenue_at_risk_minor=event.amount_minor,
                currency=event.currency,
                state=CaseState.DETECTED,
                state_version=1,
                diagnosis=None,
                diagnosis_confidence=None,
                retry_count=0,
                contact_count=0,
                created_at=evaluated_at,
                updated_at=evaluated_at,
            )
            case_row = await self._repository.create_case(
                case,
                recovery_episode_key=identity.episode_key,
                latest_evidence_event_id=event_row.id,
                latest_evidence_occurred_at=event.occurred_at,
            )
            link = await self._repository.link_evidence(
                merchant_id=merchant_id,
                normalized_event_id=event_row.id,
                recovery_case_id=case.case_id,
                disposition=EvidenceDisposition.APPLIED,
                reason_code="EVIDENCE_ACCEPTED",
            )
        else:
            authoritative = await self._repository.authoritative_facts(event_row)
            latest = None
            if case_row.latest_evidence_event_id is not None:
                latest = await self._repository.get_normalized_event(
                    merchant_id=merchant_id,
                    normalized_event_id=case_row.latest_evidence_event_id,
                )
            ordering = order_evidence(
                event_occurred_at=event.occurred_at,
                event_id=event.event_id,
                event_status=_event_status(event.event_type),
                authoritative=authoritative,
                case_watermark_at=case_row.latest_evidence_occurred_at,
                case_watermark_event_id=case_row.latest_evidence_event_id,
                case_status=_event_status(latest.event_type) if latest else None,
            )
            link = await self._repository.link_evidence(
                merchant_id=merchant_id,
                normalized_event_id=event_row.id,
                recovery_case_id=case_row.id,
                disposition=ordering.disposition,
                reason_code=ordering.reason_code,
            )
            if not link.created:
                return RecoveryServiceResult(
                    normalized_event_id=event_row.id,
                    case_id=link.link.recovery_case_id,
                    case_state=CaseState(case_row.state),
                    disposition=EvidenceDisposition(link.link.disposition),
                    reason_code="EVENT_ALREADY_LINKED",
                )
            if ordering.disposition is not EvidenceDisposition.APPLIED:
                return RecoveryServiceResult(
                    normalized_event_id=event_row.id,
                    case_id=case_row.id,
                    case_state=CaseState(case_row.state),
                    disposition=ordering.disposition,
                    reason_code=ordering.reason_code,
                )
            case = await self._require_case(merchant_id=merchant_id, case_id=case_row.id)

        if not link.created:
            return RecoveryServiceResult(
                normalized_event_id=event_row.id,
                case_id=case.case_id,
                case_state=case.state,
                disposition=EvidenceDisposition(link.link.disposition),
                reason_code="EVENT_ALREADY_LINKED",
            )
        case = await self._advance_to_decision_pending(
            case=case,
            diagnosis=diagnosis,
            event=event,
            policy=policy,
            evaluated_at=evaluated_at,
        )
        if case.state is not CaseState.DECISION_PENDING:
            return RecoveryServiceResult(
                normalized_event_id=event_row.id,
                case_id=case.case_id,
                case_state=case.state,
                disposition=EvidenceDisposition.APPLIED,
                reason_code="CASE_NOT_ELIGIBLE_FOR_REEVALUATION",
            )
        return await self._evaluate_case(
            case=case,
            diagnosis=diagnosis,
            event_row=event_row,
            policy=policy,
            evaluated_at=evaluated_at,
            approval=None,
        )

    async def reevaluate_deferred(
        self, *, due_at: datetime, limit: int
    ) -> tuple[RecoveryServiceResult, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        evaluated_at = _utc(due_at)
        rows = await self._repository.due_deferred_cases(due_at=evaluated_at, limit=limit)
        results: list[RecoveryServiceResult] = []
        for row in rows:
            if row.latest_evidence_event_id is None:
                raise LookupError("deferred case has no persisted evidence watermark")
            event_row = await self._repository.get_normalized_event(
                merchant_id=row.merchant_id,
                normalized_event_id=row.latest_evidence_event_id,
            )
            if event_row is None:
                raise LookupError("deferred case evidence does not exist")
            diagnosis = diagnose_event(_event_from_row(event_row))
            if diagnosis is None:
                raise ValueError("deferred case evidence has no recovery diagnosis")
            policy = await self._repository.effective_policy(
                merchant_id=row.merchant_id,
                evaluated_at=evaluated_at,
            )
            case = await self._require_case(merchant_id=row.merchant_id, case_id=row.id)
            case = await self._transition(
                case=case,
                to_state=CaseState.DECISION_PENDING,
                actor="SYSTEM",
                reason_code="DEFERRED_CASE_DUE",
                correlation_id=event_row.correlation_id,
                policy=policy,
                occurred_at=evaluated_at,
            )
            results.append(
                await self._evaluate_case(
                    case=case,
                    diagnosis=diagnosis,
                    event_row=event_row,
                    policy=policy,
                    evaluated_at=evaluated_at,
                    approval=None,
                )
            )
        return tuple(results)

    async def decide_review(
        self, *, merchant_id: str, decision: HumanReviewDecision
    ) -> RecoveryServiceResult:
        evaluated_at = _utc(self._clock())
        if decision.decided_at > evaluated_at:
            raise ValueError("review decision time cannot be in the future")
        await self._repository.lock_merchant(merchant_id=merchant_id)
        request = await self._repository.get_review(
            merchant_id=merchant_id,
            review_id=decision.review_id,
            for_update=True,
        )
        if request is None:
            raise LookupError("tenant-scoped human review does not exist")
        case = await self._require_case(merchant_id=merchant_id, case_id=request.case_id)
        policy = await self._repository.effective_policy(
            merchant_id=merchant_id,
            evaluated_at=evaluated_at,
        )
        if evaluated_at >= request.expires_at:
            decided = expire_review(request, expired_at=evaluated_at)
            await self._repository.update_review_decision(
                merchant_id=merchant_id,
                review=decided,
            )
            stopped = await self._transition(
                case=case,
                to_state=CaseState.STOPPED,
                actor=decision.reviewer_id,
                reason_code="HUMAN_REVIEW_EXPIRED",
                correlation_id=request.review_id,
                policy=policy,
                occurred_at=evaluated_at,
                terminal_reason="HUMAN_REVIEW_EXPIRED",
            )
            return RecoveryServiceResult(
                normalized_event_id=None,
                case_id=stopped.case_id,
                case_state=stopped.state,
                disposition=None,
                reason_code="HUMAN_REVIEW_EXPIRED",
                review_id=request.review_id,
            )

        decided = apply_review_decision(request, decision)
        await self._repository.update_review_decision(merchant_id=merchant_id, review=decided)
        if decision.decision is ReviewDecisionType.REJECT:
            stopped = await self._transition(
                case=case,
                to_state=CaseState.STOPPED,
                actor=decision.reviewer_id,
                reason_code="HUMAN_REVIEW_REJECTED",
                correlation_id=request.review_id,
                policy=policy,
                occurred_at=evaluated_at,
                terminal_reason="HUMAN_REVIEW_REJECTED",
            )
            return RecoveryServiceResult(
                normalized_event_id=None,
                case_id=stopped.case_id,
                case_state=stopped.state,
                disposition=None,
                reason_code="HUMAN_REVIEW_REJECTED",
                review_id=request.review_id,
            )

        row = await self._repository.get_case_record(
            merchant_id=merchant_id,
            case_id=case.case_id,
            for_update=True,
        )
        if row is None or row.latest_evidence_event_id is None:
            raise LookupError("reviewed case has no persisted evidence")
        event_row = await self._repository.get_normalized_event(
            merchant_id=merchant_id,
            normalized_event_id=row.latest_evidence_event_id,
        )
        if event_row is None:
            raise LookupError("reviewed case evidence does not exist")
        diagnosis = diagnose_event(_event_from_row(event_row)) or _no_action_diagnosis(case)
        pending = await self._transition(
            case=case,
            to_state=CaseState.DECISION_PENDING,
            actor=decision.reviewer_id,
            reason_code="HUMAN_REVIEW_APPROVED",
            correlation_id=request.review_id,
            policy=policy,
            occurred_at=evaluated_at,
        )
        return await self._evaluate_case(
            case=pending,
            diagnosis=diagnosis,
            event_row=event_row,
            policy=policy,
            evaluated_at=evaluated_at,
            approval=decided,
        )

    async def _link_audit_only(
        self,
        *,
        event_row: NormalizedEvent,
        case_id: str | None,
        case_state: CaseState | None,
        reason_code: str,
    ) -> RecoveryServiceResult:
        link = await self._repository.link_evidence(
            merchant_id=event_row.merchant_id,
            normalized_event_id=event_row.id,
            recovery_case_id=case_id,
            disposition=EvidenceDisposition.AUDIT_ONLY,
            reason_code=reason_code,
        )
        return RecoveryServiceResult(
            normalized_event_id=event_row.id,
            case_id=case_id,
            case_state=case_state,
            disposition=EvidenceDisposition(link.link.disposition),
            reason_code=reason_code if link.created else "EVENT_ALREADY_LINKED",
        )

    async def _advance_to_decision_pending(
        self,
        *,
        case: RecoveryCase,
        diagnosis: Diagnosis,
        event: RevenueRiskEvent,
        policy: MerchantPolicySnapshot,
        evaluated_at: datetime,
    ) -> RecoveryCase:
        if case.state is CaseState.DETECTED:
            case = await self._transition(
                case=case,
                to_state=CaseState.DIAGNOSING,
                actor="RECOVERY_SERVICE",
                reason_code="DIAGNOSIS_STARTED",
                correlation_id=event.correlation_id,
                policy=policy,
                occurred_at=evaluated_at,
            )
        if case.state is CaseState.DIAGNOSING:
            return await self._transition(
                case=case,
                to_state=CaseState.DECISION_PENDING,
                actor="RECOVERY_SERVICE",
                reason_code="DIAGNOSIS_COMPLETED",
                correlation_id=event.correlation_id,
                policy=policy,
                occurred_at=evaluated_at,
                diagnosis=diagnosis,
                latest_event=event,
            )
        if case.state is CaseState.DEFERRED:
            return await self._transition(
                case=case,
                to_state=CaseState.DECISION_PENDING,
                actor="RECOVERY_SERVICE",
                reason_code="NEW_EVIDENCE_REEVALUATION",
                correlation_id=event.correlation_id,
                policy=policy,
                occurred_at=evaluated_at,
                diagnosis=diagnosis,
                latest_event=event,
            )
        if case.state is CaseState.DECISION_PENDING:
            return replace(
                case,
                diagnosis=diagnosis.code,
                diagnosis_confidence=diagnosis.confidence_basis_points / 10_000,
            )
        return case

    async def _evaluate_case(
        self,
        *,
        case: RecoveryCase,
        diagnosis: Diagnosis,
        event_row: NormalizedEvent,
        policy: MerchantPolicySnapshot,
        evaluated_at: datetime,
        approval: HumanReviewRequest | None,
    ) -> RecoveryServiceResult:
        intelligence = await self._case_intelligence.recommend(
            self._intelligence_request(
                case=case,
                diagnosis=diagnosis,
                event_row=event_row,
                evaluated_at=evaluated_at,
            )
        )
        advisory_diagnosis = Diagnosis(
            code=diagnosis.code if diagnosis.terminal else intelligence.diagnosis_code,
            confidence_basis_points=min(
                diagnosis.confidence_basis_points,
                intelligence.confidence_basis_points,
            ),
            candidates=intelligence.candidates,
            defer_until=diagnosis.defer_until,
            terminal=diagnosis.terminal,
        )
        checking = await self._transition(
            case=case,
            to_state=CaseState.POLICY_CHECK,
            actor="RECOVERY_SERVICE",
            reason_code="POLICY_EVALUATION_STARTED",
            correlation_id=event_row.correlation_id,
            policy=policy,
            occurred_at=evaluated_at,
            diagnosis=advisory_diagnosis,
            latest_event=_event_from_row(event_row),
        )
        consent, opted_out = await self._repository.consent_facts(
            merchant_id=case.merchant_id,
            customer_id=case.customer_id,
        )
        incidents = await self._repository.active_incidents(
            merchant_id=case.merchant_id,
            evaluated_at=evaluated_at,
            case_id=checking.case_id,
            diagnosis_code=advisory_diagnosis.code,
        )
        linked_systemic_incident = next(
            (
                incident
                for incident in incidents
                if incident.scope
                in {
                    IncidentScope.PAYMENT_RAIL,
                    IncidentScope.GATEWAY,
                    IncidentScope.ISSUER,
                }
            ),
            None,
        )
        if linked_systemic_incident is not None:
            checking = replace(
                checking,
                active_incident_id=linked_systemic_incident.incident_id,
            )
        aggregate_contact_count = case.contact_count
        active_case_ids: tuple[str, ...] = (case.case_id,)
        active_intervention_id = None
        if case.customer_id is not None:
            contact_snapshot = await self._repository.customer_contact_snapshot(
                merchant_id=case.merchant_id,
                customer_id=case.customer_id,
                for_update=True,
            )
            aggregate_contact_count = contact_snapshot.aggregate_contact_count
            active_case_ids = contact_snapshot.active_case_ids or (case.case_id,)
            active_intervention_id = contact_snapshot.active_intervention_id
            if active_intervention_id is not None:
                await self._repository.coordinate_case_with_active_intervention(
                    merchant_id=case.merchant_id,
                    customer_id=case.customer_id,
                    case_id=case.case_id,
                    updated_at=evaluated_at,
                )
        try:
            scoring = rank_candidates_by_expected_net_recovery(
                advisory_diagnosis.candidates,
                context=RecoveryScoringContext(
                    amount_minor=case.revenue_at_risk_minor,
                    retry_count=case.retry_count,
                    aggregate_contact_count=aggregate_contact_count,
                    diagnosis_confidence_basis_points=(advisory_diagnosis.confidence_basis_points),
                    failure_category=event_row.normalized_failure_category,
                    active_systemic_incident=bool(incidents),
                    evaluated_at=evaluated_at,
                ),
                artifact=self._scoring_artifact,
                economics=self._action_economics,
                allowed_actions=policy.allowed_actions,
            )
        except ArithmeticError, ValueError:
            scoring = RecoveryScoringResult(
                candidates=advisory_diagnosis.candidates,
                model_version=self._scoring_artifact.model_version,
                feature_version=self._scoring_artifact.feature_version,
                economics_version=self._action_economics.version,
                artifact_classification=self._scoring_artifact.classification,
                fallback_reason="SCORING_INFERENCE_FAILED",
            )
        ranked_diagnosis = replace(advisory_diagnosis, candidates=scoring.candidates)
        facts = await self._repository.authoritative_facts_for_case(
            merchant_id=case.merchant_id,
            case=checking,
        )
        status = (facts.status or "").upper()
        evaluation = PolicyEvaluationInput(
            case_id=case.case_id,
            amount_minor=case.revenue_at_risk_minor,
            currency=case.currency,
            confidence_basis_points=advisory_diagnosis.confidence_basis_points,
            retry_count=case.retry_count,
            contact_count=aggregate_contact_count,
            evaluated_at=evaluated_at,
            candidates=ranked_diagnosis.candidates,
            evidence_references=(event_row.id,),
            consent_by_channel=consent,
            opted_out_channels=opted_out,
            incidents=incidents,
            already_paid=status in _PAID_STATUSES,
            disputed=(status == "DISPUTED" or diagnosis.code == "PAYMENT_DISPUTED"),
            cancelled=status in _CANCELLED_STATUSES,
            diagnosis_defer_until=ranked_diagnosis.defer_until,
            approval=approval,
            unknown_equivalent_action=await self._has_unknown_equivalent(
                case=checking,
                candidates=ranked_diagnosis.candidates,
            ),
            customer_contact_in_progress=active_intervention_id is not None,
            customer_identity_resolved=case.customer_id is not None,
            active_promise_to_pay=facts.promise_due_at is not None,
            promise_due_at=facts.promise_due_at,
        )
        decision = evaluate_policy(policy, evaluation)
        review = None
        if decision.result is PolicyResult.REQUIRE_HUMAN:
            review = self._build_review(
                case=checking,
                policy=policy,
                decision=decision,
                evidence_references=evaluation.evidence_references,
                requested_at=evaluated_at,
            )
            await self._repository.store_review(review)
        terminal_reason = (
            decision.reason_codes[-1] if decision.result is PolicyResult.STOP else None
        )
        decided_case = await self._transition(
            case=checking,
            to_state=decision.resulting_state,
            actor="POLICY_ENGINE",
            reason_code=decision.reason_codes[-1],
            reason_detail=",".join(decision.reason_codes),
            correlation_id=event_row.correlation_id,
            policy=policy,
            occurred_at=evaluated_at,
            terminal_reason=terminal_reason,
            next_evaluation_at=decision.next_evaluation_at,
            increment_retry=(
                decision.result is PolicyResult.DEFER
                and decision.selected_action.action_type is ActionType.DEFER_RETRY
                and decision.reason_codes[-1] == "LOW_CONFIDENCE_RETRY_DEFERRED"
            ),
        )
        receipt_id = self._id_generator("receipt")
        action = self._build_action(
            case=decided_case,
            decision=decision,
            receipt_id=receipt_id,
            authorized_at=evaluated_at,
            coordinated_case_ids=active_case_ids,
        )
        receipt = DecisionReceipt(
            receipt_id=receipt_id,
            case_id=case.case_id,
            merchant_id=case.merchant_id,
            correlation_id=event_row.correlation_id,
            evidence_references=evaluation.evidence_references,
            candidate_actions=ranked_diagnosis.candidates,
            selected_action_type=decision.selected_action.action_type,
            explanation=(
                intelligence.explanation + " Policy evaluation: " + ",".join(decision.reason_codes)
            ),
            policy_result=decision.result,
            policy_reason_codes=decision.reason_codes,
            versions=VersionBundle(
                policy=policy.version,
                features=intelligence.feature_version,
                application=self._application_version,
                model=intelligence.model_version,
                prompt=intelligence.prompt_version,
            ),
            created_at=evaluated_at,
            resulting_state=decided_case.state,
            human_review_id=review.review_id if review else None,
            resulting_action_id=action.action_id if action else None,
            model_prediction_ids=tuple(
                prediction.prediction_id for prediction in intelligence.predictions
            ),
            scoring_model_version=scoring.model_version,
            scoring_feature_version=scoring.feature_version,
            scoring_economics_version=scoring.economics_version,
            scoring_artifact_classification=scoring.artifact_classification.value,
            scoring_fallback_reason=scoring.fallback_reason,
        )
        await self._repository.store_model_predictions(intelligence.predictions)
        await self._repository.store_receipt(receipt)
        if action is not None:
            if self._action_repository is None:
                raise RuntimeError("PROCEED requires the durable action repository")
            await self._action_repository.store_action(
                action,
                policy_version=policy.version,
                correlation_id=event_row.correlation_id,
                max_attempts=3,
                reconciliation_deadline=evaluated_at + timedelta(hours=1),
            )
            if (
                ACTION_CLASSES[action.action_type] is ActionClass.CUSTOMER_CONTACT
                and case.customer_id is not None
            ):
                intervention_material = ":".join(
                    (case.merchant_id, case.customer_id, action.action_id)
                )
                await self._repository.store_customer_intervention(
                    CustomerIntervention(
                        intervention_id=(
                            "intervention_"
                            + sha256(intervention_material.encode()).hexdigest()[:32]
                        ),
                        merchant_id=case.merchant_id,
                        customer_id=case.customer_id,
                        owner_case_id=case.case_id,
                        action_id=action.action_id,
                        coordinated_case_ids=active_case_ids,
                        status=CustomerInterventionStatus.ACTIVE,
                        cooldown_until=evaluated_at
                        + timedelta(seconds=policy.default_defer_seconds),
                        model_version=scoring.model_version,
                        policy_version=policy.version,
                        created_at=evaluated_at,
                        updated_at=evaluated_at,
                    )
                )
        return RecoveryServiceResult(
            normalized_event_id=event_row.id,
            case_id=decided_case.case_id,
            case_state=decided_case.state,
            disposition=EvidenceDisposition.APPLIED,
            reason_code=decision.reason_codes[-1],
            receipt_id=receipt.receipt_id,
            review_id=review.review_id if review else None,
            action_id=action.action_id if action else None,
        )

    @staticmethod
    def _intelligence_request(
        *,
        case: RecoveryCase,
        diagnosis: Diagnosis,
        event_row: NormalizedEvent,
        evaluated_at: datetime,
    ) -> CaseIntelligenceRequest:
        material = ":".join(
            (
                case.case_id,
                event_row.correlation_id,
                str(case.state_version),
                evaluated_at.isoformat(),
            )
        )
        return CaseIntelligenceRequest(
            run_id=f"intelligence_{sha256(material.encode()).hexdigest()[:32]}",
            case_id=case.case_id,
            merchant_id=case.merchant_id,
            correlation_id=event_row.correlation_id,
            workflow_type=case.workflow_type,
            subject_type=case.subject_type,
            target=diagnosis.candidates[0].target,
            amount_minor=case.revenue_at_risk_minor,
            currency=case.currency,
            diagnosis_code=diagnosis.code,
            diagnosis_confidence_basis_points=diagnosis.confidence_basis_points,
            candidates=diagnosis.candidates,
            retry_count=case.retry_count,
            contact_count=case.contact_count,
            evidence=(
                EvidenceItem(
                    reference=event_row.id,
                    event_type=event_row.event_type,
                    failure_category=event_row.normalized_failure_category,
                    summary=(
                        "A normalized provider event reports "
                        f"{event_row.normalized_failure_category} for this case."
                    ),
                    occurred_at=event_row.occurred_at,
                ),
            ),
            feature_version="phase5-case-features-1.0",
            evaluated_at=evaluated_at,
            terminal_diagnosis=diagnosis.terminal,
        )

    async def _has_unknown_equivalent(
        self,
        *,
        case: RecoveryCase,
        candidates: tuple[CandidateAction, ...],
    ) -> bool:
        if self._action_repository is None:
            return False
        for candidate in candidates:
            if candidate.action_type is ActionType.NO_ACTION:
                continue
            if await self._action_repository.has_unknown_equivalent(
                merchant_id=case.merchant_id,
                recovery_case_id=case.case_id,
                action_type=candidate.action_type,
                target_id=candidate.target,
            ):
                return True
        return False

    def _build_action(
        self,
        *,
        case: RecoveryCase,
        decision: PolicyDecision,
        receipt_id: str,
        authorized_at: datetime,
        coordinated_case_ids: tuple[str, ...],
    ) -> RecoveryAction | None:
        if (
            decision.result is not PolicyResult.PROCEED
            or decision.selected_action.action_type is ActionType.ESCALATE_HUMAN
        ):
            return None
        candidate = decision.selected_action
        logical_attempt = max(1, candidate.logical_attempt)
        key = action_idempotency_key(
            merchant_id=case.merchant_id,
            case_id=case.case_id,
            action_type=candidate.action_type,
            target_type=case.subject_type,
            target_id=candidate.target,
            logical_attempt=logical_attempt,
        )
        parameters: dict[str, object] = {
            "amount_minor": case.revenue_at_risk_minor,
            "currency": case.currency,
            "provider_mode": "TEST",
        }
        if candidate.channel is not None:
            parameters["channel"] = f"SIMULATED_{candidate.channel.value}"
            parameters["coordinated_case_ids"] = list(coordinated_case_ids)
        return RecoveryAction(
            action_id=f"action_{key.rsplit(':', maxsplit=1)[-1][:32]}",
            case_id=case.case_id,
            merchant_id=case.merchant_id,
            decision_receipt_id=receipt_id,
            action_type=candidate.action_type,
            target_type=case.subject_type,
            target_id=candidate.target,
            logical_attempt=logical_attempt,
            idempotency_key=key,
            status=ActionStatus.PENDING,
            parameters=parameters,
            authorized_at=authorized_at,
            execute_after=authorized_at,
            created_at=authorized_at,
        )

    async def _transition(
        self,
        *,
        case: RecoveryCase,
        to_state: CaseState,
        actor: str,
        reason_code: str,
        correlation_id: str,
        policy: MerchantPolicySnapshot,
        occurred_at: datetime,
        reason_detail: str | None = None,
        terminal_reason: str | None = None,
        next_evaluation_at: datetime | None = None,
        diagnosis: Diagnosis | None = None,
        latest_event: RevenueRiskEvent | None = None,
        increment_retry: bool = False,
    ) -> RecoveryCase:
        updated, transition = transition_case(
            case,
            expected_version=case.state_version,
            to_state=to_state,
            actor=actor,
            reason_code=reason_code,
            reason_detail=reason_detail,
            correlation_id=correlation_id,
            policy_version=policy.version,
            occurred_at=occurred_at,
            terminal_reason=terminal_reason,
            next_evaluation_at=next_evaluation_at,
        )
        if diagnosis is not None:
            updated = replace(
                updated,
                diagnosis=diagnosis.code,
                diagnosis_confidence=diagnosis.confidence_basis_points / 10_000,
            )
        if increment_retry:
            updated = replace(updated, retry_count=updated.retry_count + 1)
        await self._repository.apply_transition(
            updated_case=updated,
            transition=transition,
            latest_evidence_event_id=latest_event.event_id if latest_event else None,
            latest_evidence_occurred_at=latest_event.occurred_at if latest_event else None,
        )
        return updated

    async def _require_case(self, *, merchant_id: str, case_id: str) -> RecoveryCase:
        case = await self._repository.get_case(
            merchant_id=merchant_id,
            case_id=case_id,
            for_update=True,
        )
        if case is None:
            raise LookupError("tenant-scoped recovery case does not exist")
        return case

    def _build_review(
        self,
        *,
        case: RecoveryCase,
        policy: MerchantPolicySnapshot,
        decision: PolicyDecision,
        evidence_references: tuple[str, ...],
        requested_at: datetime,
    ) -> HumanReviewRequest:
        candidate = decision.selected_action
        fingerprint = ActionFingerprintInput(
            case_id=case.case_id,
            action_type=candidate.action_type.value,
            target=candidate.target,
            amount_minor=case.revenue_at_risk_minor,
            currency=case.currency,
            logical_attempt=candidate.logical_attempt,
            policy_digest=policy.content_digest,
        ).digest()
        return HumanReviewRequest(
            review_id=self._id_generator("review"),
            merchant_id=case.merchant_id,
            case_id=case.case_id,
            action_fingerprint=fingerprint,
            proposed_action_type=candidate.action_type.value,
            evidence_references=evidence_references,
            policy_version=policy.version,
            policy_digest=policy.content_digest,
            reason_code=decision.reason_codes[-1],
            risk_detail="Policy requires explicit operator approval",
            requested_at=requested_at,
            expires_at=requested_at + self._review_ttl,
            status=ReviewStatus.REQUESTED,
        )


def _event_from_row(row: NormalizedEvent) -> RevenueRiskEvent:
    return RevenueRiskEvent(
        event_id=row.id,
        merchant_id=row.merchant_id,
        source=EventSource(row.source),
        source_event_id=row.source_event_id,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        received_at=row.received_at,
        customer_id=row.customer_id,
        payment_id=row.payment_id,
        order_id=row.order_id,
        subscription_id=row.subscription_id,
        invoice_id=row.invoice_id,
        payment_link_id=row.payment_link_id,
        amount_minor=row.amount_minor,
        currency=row.currency,
        failure_code=row.failure_code,
        normalized_failure_category=NormalizedFailureCategory(row.normalized_failure_category),
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        source_payload_reference=row.source_payload_reference,
        schema_version=row.schema_version,
    )


def _event_status(event_type: str) -> str:
    return event_type.rsplit(".", maxsplit=1)[-1].upper()


def _no_action_diagnosis(case: RecoveryCase) -> Diagnosis:
    return Diagnosis(
        code=case.diagnosis or "NO_RECOVERY_DIAGNOSIS",
        confidence_basis_points=round((case.diagnosis_confidence or 0) * 10_000),
        candidates=(
            CandidateAction(
                action_type=ActionType.NO_ACTION,
                recovery_probability_basis_points=0,
                expected_net_recovery_minor=0,
                rank=1,
                target=case.subject_id,
            ),
        ),
    )


def _new_id(prefix: str) -> str:
    material = f"{prefix}:{uuid4().hex}"
    return f"{prefix}_{sha256(material.encode()).hexdigest()[:32]}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
