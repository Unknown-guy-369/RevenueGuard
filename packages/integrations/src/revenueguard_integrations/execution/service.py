"""Application services for execution, uncertainty, and authoritative verification."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Final

from revenueguard_domain import (
    ACTION_CLASSES,
    ActionClass,
    ActionStatus,
    ActionType,
    CandidateAction,
    CaseState,
    ContactChannel,
    EvidenceSource,
    PolicyDecision,
    PolicyEvaluationInput,
    PolicyResult,
    RecoveryAction,
    RecoveryCase,
    RevenueRiskEvent,
    VerifiedOutcome,
    evaluate_policy,
    transition_case,
)

from revenueguard_integrations.execution.providers import (
    ProviderExecutionResult,
    ProviderLookupResult,
)
from revenueguard_integrations.persistence import ActionRepository, RecoveryRepository
from revenueguard_integrations.persistence.models import RecoveryAction as RecoveryActionRow

_SIGNED_SUCCESS_EVENTS: Final = frozenset(
    {"payment_link.paid", "payment.captured", "subscription.charged"}
)


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    action: RecoveryAction
    attempt_id: str


@dataclass(frozen=True, slots=True)
class ExecutionDisposition:
    action_id: str
    action_status: ActionStatus
    case_state: CaseState
    reason_code: str


class ActionExecutionService:
    def __init__(
        self,
        action_repository: ActionRepository,
        recovery_repository: RecoveryRepository,
        *,
        retry_base: timedelta = timedelta(seconds=5),
        unknown_ttl: timedelta = timedelta(hours=1),
    ) -> None:
        if retry_base <= timedelta(0) or unknown_ttl <= timedelta(0):
            raise ValueError("retry and unknown durations must be positive")
        self._actions = action_repository
        self._recovery = recovery_repository
        self._retry_base = retry_base
        self._unknown_ttl = unknown_ttl

    async def prepare_execution(
        self,
        *,
        merchant_id: str,
        action_id: str,
        lease_token: str,
        started_at: datetime,
    ) -> PreparedExecution | ExecutionDisposition:
        now = _utc(started_at)
        action_row = await self._actions.get_action(
            merchant_id=merchant_id,
            action_id=action_id,
            for_update=True,
        )
        if action_row is None:
            raise LookupError("tenant-scoped recovery action does not exist")
        case = await self._recovery.get_case(
            merchant_id=merchant_id,
            case_id=action_row.recovery_case_id,
            for_update=True,
        )
        if case is None:
            raise LookupError("recovery action case does not exist")
        if case.state not in {CaseState.READY, CaseState.EXECUTING}:
            raise ValueError("case is not executable")
        if case.state is CaseState.READY:
            policy_decision = await self._final_policy_decision(
                action=action_row,
                case=case,
                evaluated_at=now,
            )
            if (
                policy_decision.result is not PolicyResult.PROCEED
                or policy_decision.selected_action.action_type.value != action_row.action_type
            ):
                reason_code = f"PRE_EXECUTION_{policy_decision.reason_codes[-1]}"
                await self._actions.cancel_before_execution(
                    action=action_row,
                    cancelled_at=now,
                    reason_code=reason_code,
                )
                case = await self._transition(
                    case=case,
                    action=action_row,
                    to_state=CaseState.EXECUTING,
                    reason_code="PRE_EXECUTION_POLICY_BLOCKED",
                    occurred_at=now,
                )
                case = await self._transition(
                    case=case,
                    action=action_row,
                    to_state=CaseState.VERIFYING,
                    reason_code=reason_code,
                    occurred_at=now,
                )
                if policy_decision.result is PolicyResult.STOP:
                    case = await self._transition(
                        case=case,
                        action=action_row,
                        to_state=CaseState.STOPPED,
                        reason_code=reason_code,
                        occurred_at=now,
                        terminal_reason=reason_code,
                    )
                else:
                    case = await self._transition(
                        case=case,
                        action=action_row,
                        to_state=CaseState.DECISION_PENDING,
                        reason_code=reason_code,
                        occurred_at=now,
                    )
                return ExecutionDisposition(
                    action_row.id,
                    ActionStatus.FAILED,
                    case.state,
                    reason_code,
                )
        _, attempt = await self._actions.begin_attempt(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            started_at=now,
        )
        if case.state is CaseState.READY:
            updated, transition = transition_case(
                case,
                expected_version=case.state_version,
                to_state=CaseState.EXECUTING,
                actor="ACTION_EXECUTOR",
                reason_code="ACTION_EXECUTION_STARTED",
                correlation_id=action_row.correlation_id,
                policy_version=action_row.policy_version,
                occurred_at=now,
            )
            action_class = ACTION_CLASSES[ActionType(action_row.action_type)]
            if action_class is ActionClass.RETRY:
                updated = replace(updated, retry_count=updated.retry_count + 1)
            if action_class is ActionClass.CUSTOMER_CONTACT:
                updated = replace(updated, contact_count=updated.contact_count + 1)
            await self._recovery.apply_transition(updated_case=updated, transition=transition)
        domain_action = await self._actions.domain_action(
            merchant_id=merchant_id,
            action_id=action_id,
        )
        if domain_action is None:
            raise AssertionError("locked action disappeared")
        return PreparedExecution(domain_action, attempt.id)

    async def _final_policy_decision(
        self,
        *,
        action: RecoveryActionRow,
        case: RecoveryCase,
        evaluated_at: datetime,
    ) -> PolicyDecision:
        receipt = await self._recovery.get_decision_receipt(
            merchant_id=action.merchant_id,
            receipt_id=action.decision_receipt_id,
        )
        if receipt is None:
            raise LookupError("action decision receipt does not exist")
        selected_document = next(
            (
                candidate
                for candidate in receipt.candidate_actions
                if candidate.get("action_type") == action.action_type
            ),
            None,
        )
        if selected_document is None:
            raise ValueError("action is not present in its authorization receipt")
        channel_value = selected_document.get("channel")
        candidate = CandidateAction(
            action_type=ActionType(action.action_type),
            recovery_probability_basis_points=round(
                float(selected_document["recovery_probability"]) * 10_000
            ),
            expected_net_recovery_minor=int(selected_document["expected_net_recovery_minor"]),
            rank=1,
            target=action.target_id,
            logical_attempt=action.logical_attempt,
            channel=ContactChannel(str(channel_value)) if channel_value is not None else None,
        )
        no_action = CandidateAction(
            action_type=ActionType.NO_ACTION,
            recovery_probability_basis_points=0,
            expected_net_recovery_minor=0,
            rank=2,
            target=action.target_id,
        )
        policy = await self._recovery.effective_policy(
            merchant_id=action.merchant_id,
            evaluated_at=evaluated_at,
        )
        consent, opted_out = await self._recovery.consent_facts(
            merchant_id=action.merchant_id,
            customer_id=case.customer_id,
        )
        incidents = await self._recovery.active_incidents(
            merchant_id=action.merchant_id,
            evaluated_at=evaluated_at,
        )
        facts = await self._recovery.authoritative_facts_for_case(
            merchant_id=action.merchant_id,
            case=case,
        )
        status = (facts.status or "").upper()
        approval = None
        if receipt.human_review_id is not None:
            approval = await self._recovery.get_review(
                merchant_id=action.merchant_id,
                review_id=receipt.human_review_id,
            )
        return evaluate_policy(
            policy,
            PolicyEvaluationInput(
                case_id=case.case_id,
                amount_minor=case.revenue_at_risk_minor,
                currency=case.currency,
                confidence_basis_points=round((case.diagnosis_confidence or 0) * 10_000),
                retry_count=case.retry_count,
                contact_count=case.contact_count,
                evaluated_at=evaluated_at,
                candidates=(candidate, no_action),
                evidence_references=tuple(receipt.evidence_references),
                consent_by_channel=consent,
                opted_out_channels=opted_out,
                incidents=incidents,
                already_paid=status in {"CAPTURED", "CHARGED", "COMPLETED", "PAID"},
                disputed=status == "DISPUTED",
                cancelled=status in {"CANCELLED", "ESCALATED"},
                approval=approval,
                active_promise_to_pay=facts.promise_due_at is not None,
                promise_due_at=facts.promise_due_at,
            ),
        )

    async def record_execution_result(
        self,
        *,
        merchant_id: str,
        action_id: str,
        lease_token: str,
        result: ProviderExecutionResult,
    ) -> ExecutionDisposition:
        completed_at = result.observed_at
        action_before = await self._actions.get_action(
            merchant_id=merchant_id,
            action_id=action_id,
            for_update=True,
        )
        if action_before is None:
            raise LookupError("tenant-scoped recovery action does not exist")
        exponent = max(action_before.attempt_count - 1, 0)
        retry_at = completed_at + min(
            self._retry_base * (2**exponent),
            timedelta(minutes=5),
        )
        action, _ = await self._actions.finish_attempt(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            completed_at=completed_at,
            outcome_status=result.status,
            response_category=result.response_category,
            provider_object_id=result.provider_object_id,
            provider_status_code=result.provider_status_code,
            error_code=result.error_code,
            response_reference=result.response_reference,
            retryable=result.retryable,
            retry_at=retry_at,
            reconciliation_deadline=completed_at + self._unknown_ttl,
        )
        case = await self._required_case(action)
        if action.status == ActionStatus.PENDING.value:
            return ExecutionDisposition(
                action.id,
                ActionStatus.PENDING,
                case.state,
                "EXPLICIT_FAILURE_RETRY_SCHEDULED",
            )
        if result.status is ActionStatus.UNKNOWN:
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.UNKNOWN,
                reason_code=result.error_code or "PROVIDER_RESULT_UNKNOWN",
                occurred_at=completed_at,
            )
            await self._store_observation(
                action=action,
                status=ActionStatus.UNKNOWN,
                source=result.evidence_source,
                observed_at=completed_at,
                evidence_reference=result.response_reference,
                reason_code=result.error_code or "PROVIDER_RESULT_UNKNOWN",
                authoritative=False,
            )
            return ExecutionDisposition(action.id, ActionStatus.UNKNOWN, case.state, "UNKNOWN")
        if result.status is ActionStatus.SUCCEEDED:
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.VERIFYING,
                reason_code="PROVIDER_REQUEST_ACCEPTED",
                occurred_at=completed_at,
            )
            await self._store_observation(
                action=action,
                status=ActionStatus.PENDING,
                source=result.evidence_source,
                observed_at=completed_at,
                evidence_reference=result.response_reference,
                reason_code="AWAITING_AUTHORITATIVE_OUTCOME",
                authoritative=False,
            )
            return ExecutionDisposition(action.id, ActionStatus.SUCCEEDED, case.state, "VERIFYING")
        case = await self._transition(
            case=case,
            action=action,
            to_state=CaseState.VERIFYING,
            reason_code="PROVIDER_REQUEST_REJECTED",
            occurred_at=completed_at,
        )
        await self._store_observation(
            action=action,
            status=ActionStatus.FAILED,
            source=result.evidence_source,
            observed_at=completed_at,
            evidence_reference=result.response_reference,
            reason_code=result.error_code or "PROVIDER_REQUEST_REJECTED",
            authoritative=True,
        )
        case = await self._transition(
            case=case,
            action=action,
            to_state=CaseState.DECISION_PENDING,
            reason_code="VERIFIED_ACTION_FAILURE",
            occurred_at=completed_at,
        )
        return ExecutionDisposition(action.id, ActionStatus.FAILED, case.state, "FAILED")

    async def mark_stale_calls_unknown(
        self, *, now: datetime, limit: int
    ) -> tuple[ExecutionDisposition, ...]:
        observed_at = _utc(now)
        rows = await self._actions.stale_inflight_actions(now=observed_at, limit=limit)
        dispositions: list[ExecutionDisposition] = []
        for action in rows:
            await self._actions.mark_stale_inflight_unknown(
                action=action,
                now=observed_at,
                reconciliation_deadline=observed_at + self._unknown_ttl,
            )
            case = await self._required_case(action)
            if case.state is CaseState.EXECUTING:
                case = await self._transition(
                    case=case,
                    action=action,
                    to_state=CaseState.UNKNOWN,
                    reason_code="INCOMPLETE_PROVIDER_CALL",
                    occurred_at=observed_at,
                )
            await self._store_observation(
                action=action,
                status=ActionStatus.UNKNOWN,
                source=EvidenceSource.NONE,
                observed_at=observed_at,
                evidence_reference=None,
                reason_code="INCOMPLETE_PROVIDER_CALL",
                authoritative=False,
            )
            dispositions.append(
                ExecutionDisposition(action.id, ActionStatus.UNKNOWN, case.state, "UNKNOWN")
            )
        return tuple(dispositions)

    async def record_lookup(
        self,
        *,
        merchant_id: str,
        action_id: str,
        result: ProviderLookupResult,
    ) -> ExecutionDisposition:
        action = await self._actions.get_action(
            merchant_id=merchant_id,
            action_id=action_id,
            for_update=True,
        )
        if action is None:
            raise LookupError("tenant-scoped recovery action does not exist")
        case = await self._required_case(action)
        if case.state not in {CaseState.UNKNOWN, CaseState.VERIFYING}:
            return ExecutionDisposition(
                action.id, ActionStatus(action.status), case.state, "STALE_LOOKUP"
            )
        if (
            case.state is CaseState.UNKNOWN
            and action.reconciliation_deadline is not None
            and result.observed_at >= action.reconciliation_deadline
        ):
            action.dead_lettered_at = result.observed_at
            action.updated_at = result.observed_at
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.ESCALATED,
                reason_code="RECONCILIATION_DEADLINE_EXCEEDED",
                occurred_at=result.observed_at,
            )
            return ExecutionDisposition(action.id, ActionStatus.UNKNOWN, case.state, "ESCALATED")
        if result.status is ActionStatus.UNKNOWN:
            return ExecutionDisposition(
                action.id,
                ActionStatus(action.status),
                case.state,
                "STILL_UNKNOWN" if case.state is CaseState.UNKNOWN else "AWAITING_VERIFICATION",
            )
        resolved_status = (
            ActionStatus.SUCCEEDED
            if result.status in {ActionStatus.SUCCEEDED, ActionStatus.PENDING}
            else ActionStatus.FAILED
        )
        if case.state is CaseState.UNKNOWN or resolved_status is ActionStatus.FAILED:
            await self._actions.set_action_resolution(
                action=action,
                status=resolved_status,
                resolved_at=result.observed_at,
                provider_object_id=result.provider_object_id,
                error_code=result.reason_code if resolved_status is ActionStatus.FAILED else None,
            )
        if case.state is CaseState.UNKNOWN:
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.VERIFYING,
                reason_code="UNKNOWN_RECONCILED",
                occurred_at=result.observed_at,
                authoritative_evidence_reference=result.evidence_reference,
            )
        outcome_status = result.status
        recovered = (
            int(action.parameters["amount_minor"])
            if outcome_status is ActionStatus.SUCCEEDED and result.is_authoritative
            else 0
        )
        await self._store_observation(
            action=action,
            status=outcome_status,
            source=result.evidence_source,
            observed_at=result.observed_at,
            evidence_reference=result.evidence_reference,
            reason_code=result.reason_code,
            authoritative=result.is_authoritative,
            recovered_amount_minor=recovered,
        )
        if outcome_status is ActionStatus.SUCCEEDED and result.is_authoritative:
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.RECOVERED,
                reason_code=result.reason_code or "AUTHORITATIVE_RECOVERY",
                occurred_at=result.observed_at,
                authoritative_evidence_reference=result.evidence_reference,
                terminal_reason=result.reason_code or "AUTHORITATIVE_RECOVERY",
            )
        elif outcome_status is ActionStatus.FAILED and result.is_authoritative:
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.DECISION_PENDING,
                reason_code=result.reason_code or "AUTHORITATIVE_ACTION_FAILURE",
                occurred_at=result.observed_at,
            )
        return ExecutionDisposition(action.id, resolved_status, case.state, "RECONCILED")

    async def verify_signed_event(
        self, *, event: RevenueRiskEvent, webhook_event_id: str
    ) -> ExecutionDisposition | None:
        if event.event_type not in _SIGNED_SUCCESS_EVENTS:
            return None
        provider_object_id = event.payment_link_id
        if provider_object_id is not None:
            action = await self._actions.find_action_for_provider_object(
                merchant_id=event.merchant_id,
                provider_object_id=provider_object_id,
                for_update=True,
            )
        else:
            subject_id = event.subscription_id or event.payment_id
            action = (
                await self._actions.find_latest_action_for_target(
                    merchant_id=event.merchant_id,
                    target_id=subject_id,
                    for_update=True,
                )
                if subject_id is not None
                else None
            )
        if action is None:
            return None
        case = await self._required_case(action)
        if case.state is CaseState.RECOVERED:
            return ExecutionDisposition(
                action.id, ActionStatus.SUCCEEDED, case.state, "ALREADY_VERIFIED"
            )
        evidence_reference = f"webhook_events/{webhook_event_id}"
        expected_amount = int(action.parameters["amount_minor"])
        expected_currency = str(action.parameters["currency"])
        if event.amount_minor != expected_amount or event.currency != expected_currency:
            if case.state is CaseState.UNKNOWN:
                case = await self._transition(
                    case=case,
                    action=action,
                    to_state=CaseState.VERIFYING,
                    reason_code="SIGNED_EVIDENCE_RECEIVED",
                    occurred_at=event.received_at,
                    authoritative_evidence_reference=evidence_reference,
                )
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.STOPPED,
                reason_code="RECOVERY_AMOUNT_MISMATCH",
                occurred_at=event.received_at,
                authoritative_evidence_reference=evidence_reference,
                terminal_reason="RECOVERY_AMOUNT_MISMATCH",
            )
            return ExecutionDisposition(
                action.id, ActionStatus.FAILED, case.state, "AMOUNT_MISMATCH"
            )
        await self._actions.set_action_resolution(
            action=action,
            status=ActionStatus.SUCCEEDED,
            resolved_at=event.received_at,
            provider_object_id=provider_object_id,
        )
        if case.state is CaseState.UNKNOWN:
            case = await self._transition(
                case=case,
                action=action,
                to_state=CaseState.VERIFYING,
                reason_code="SIGNED_EVIDENCE_RECEIVED",
                occurred_at=event.received_at,
                authoritative_evidence_reference=evidence_reference,
            )
        if case.state is not CaseState.VERIFYING:
            return ExecutionDisposition(
                action.id, ActionStatus.SUCCEEDED, case.state, "STALE_EVIDENCE"
            )
        await self._store_observation(
            action=action,
            status=ActionStatus.SUCCEEDED,
            source=EvidenceSource.SIGNED_WEBHOOK,
            observed_at=event.occurred_at,
            evidence_reference=evidence_reference,
            reason_code="PAYMENT_LINK_PAID",
            authoritative=True,
            recovered_amount_minor=event.amount_minor,
            verified_at=event.received_at,
        )
        case = await self._transition(
            case=case,
            action=action,
            to_state=CaseState.RECOVERED,
            reason_code="PAYMENT_LINK_PAID",
            occurred_at=event.received_at,
            authoritative_evidence_reference=evidence_reference,
            terminal_reason="PAYMENT_LINK_PAID",
        )
        return ExecutionDisposition(action.id, ActionStatus.SUCCEEDED, case.state, "RECOVERED")

    async def _required_case(self, action: RecoveryActionRow) -> RecoveryCase:
        case = await self._recovery.get_case(
            merchant_id=action.merchant_id,
            case_id=action.recovery_case_id,
            for_update=True,
        )
        if case is None:
            raise LookupError("recovery action case does not exist")
        return case

    async def _transition(
        self,
        *,
        case: RecoveryCase,
        action: RecoveryActionRow,
        to_state: CaseState,
        reason_code: str,
        occurred_at: datetime,
        authoritative_evidence_reference: str | None = None,
        terminal_reason: str | None = None,
    ) -> RecoveryCase:
        updated, transition = transition_case(
            case,
            expected_version=case.state_version,
            to_state=to_state,
            actor="OUTCOME_VERIFIER" if authoritative_evidence_reference else "ACTION_EXECUTOR",
            reason_code=reason_code,
            correlation_id=action.correlation_id,
            policy_version=action.policy_version,
            occurred_at=occurred_at,
            authoritative_evidence_reference=authoritative_evidence_reference,
            terminal_reason=terminal_reason,
        )
        await self._recovery.apply_transition(updated_case=updated, transition=transition)
        return updated

    async def _store_observation(
        self,
        *,
        action: RecoveryActionRow,
        status: ActionStatus,
        source: EvidenceSource,
        observed_at: datetime,
        evidence_reference: str | None,
        reason_code: str | None,
        authoritative: bool,
        recovered_amount_minor: int = 0,
        verified_at: datetime | None = None,
    ) -> None:
        reference = evidence_reference or f"{source.value}:{reason_code or status.value}"
        material = f"{action.merchant_id}:{action.id}:{source.value}:{reference}:{status.value}"
        created_at = verified_at or observed_at
        await self._actions.store_outcome(
            VerifiedOutcome(
                outcome_id=f"outcome_{sha256(material.encode()).hexdigest()[:32]}",
                action_id=action.id,
                case_id=action.recovery_case_id,
                merchant_id=action.merchant_id,
                outcome_status=status,
                is_authoritative=authoritative,
                evidence_source=source,
                evidence_reference=evidence_reference,
                provider_object_id=action.provider_object_id,
                recovered_amount_minor=recovered_amount_minor,
                currency=str(action.parameters["currency"]),
                reason_code=reason_code,
                observed_at=observed_at,
                verified_at=(verified_at or observed_at) if authoritative else None,
                created_at=created_at,
            )
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
