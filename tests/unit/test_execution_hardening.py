from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from revenueguard_domain import (
    ActionStatus,
    ActionType,
    CaseState,
    ConsentState,
    ContactChannel,
    EventSource,
    EvidenceSource,
    IncidentConstraint,
    IncidentScope,
    NormalizedFailureCategory,
    PromiseExtraction,
    PromiseIntent,
    RecoveryCase,
    RevenueRiskEvent,
    SubjectType,
    WorkflowType,
    conservative_default_policy,
)
from revenueguard_integrations.execution import (
    ActionExecutionService,
    ExecutionDisposition,
    ProviderLookupResult,
)
from revenueguard_integrations.persistence import ActionRepository, AuthoritativeFacts
from revenueguard_integrations.playbooks import ReceivablesPlaybookService

NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)


class FakeActionRepository:
    def __init__(self, action: SimpleNamespace) -> None:
        self.action = action
        self.attempts_started = 0
        self.signed_event_action_types: tuple[ActionType, ...] | None = None

    async def get_action(self, **_: object) -> SimpleNamespace:
        return self.action

    async def begin_attempt(self, **_: object) -> tuple[SimpleNamespace, SimpleNamespace]:
        self.attempts_started += 1
        self.action.attempt_count += 1
        return self.action, SimpleNamespace(id=f"attempt_{self.attempts_started}")

    async def domain_action(self, **_: object) -> object:
        return object()

    async def cancel_before_execution(
        self,
        *,
        action: SimpleNamespace,
        cancelled_at: datetime,
        reason_code: str,
    ) -> None:
        action.status = "FAILED"
        action.updated_at = cancelled_at
        action.last_error_code = reason_code

    async def mark_verification_expired_unknown(
        self, *, action: SimpleNamespace, observed_at: datetime
    ) -> None:
        action.status = "UNKNOWN"
        action.unknown_since = observed_at
        action.updated_at = observed_at

    async def store_outcome(self, outcome: object) -> object:
        return outcome

    async def find_action_for_provider_object(
        self, *, action_types: tuple[ActionType, ...], **_: object
    ) -> SimpleNamespace | None:
        self.signed_event_action_types = action_types
        return self.action if ActionType(self.action.action_type) in action_types else None

    async def find_latest_action_for_target(
        self, *, action_types: tuple[ActionType, ...], **_: object
    ) -> SimpleNamespace | None:
        self.signed_event_action_types = action_types
        return self.action if ActionType(self.action.action_type) in action_types else None


class FakeRecoveryRepository:
    def __init__(self, case: RecoveryCase) -> None:
        self.case = case
        self.policy = conservative_default_policy()
        self.incidents: tuple[IncidentConstraint, ...] = ()
        self.receipt = SimpleNamespace(
            candidate_actions=[
                {
                    "action_type": "DEFER_RETRY",
                    "recovery_probability": 0.75,
                    "expected_net_recovery_minor": 7_000,
                    "channel": None,
                }
            ],
            evidence_references=["event_failure"],
            human_review_id=None,
        )

    async def get_case(self, **_: object) -> RecoveryCase:
        return self.case

    async def get_decision_receipt(self, **_: object) -> SimpleNamespace:
        return self.receipt

    async def effective_policy(self, **_: object) -> object:
        return self.policy

    async def consent_facts(self, **_: object) -> tuple[tuple[()], frozenset[object]]:
        return (), frozenset()

    async def active_incidents(self, **_: object) -> tuple[IncidentConstraint, ...]:
        return self.incidents

    async def customer_contact_snapshot(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            aggregate_contact_count=self.case.contact_count,
            active_case_ids=(self.case.case_id,),
            active_intervention_id=None,
            active_intervention_action_id=None,
        )

    async def authoritative_facts_for_case(self, **_: object) -> AuthoritativeFacts:
        return AuthoritativeFacts(provider_updated_at=NOW, status="FAILED")

    async def get_review(self, **_: object) -> None:
        return None

    async def apply_transition(self, *, updated_case: RecoveryCase, **_: object) -> None:
        self.case = updated_case

    async def record_execution_attempt_counters(
        self,
        *,
        case: RecoveryCase,
        retry_increment: int,
        contact_increment: int,
        occurred_at: datetime,
    ) -> RecoveryCase:
        self.case = replace(
            case,
            retry_count=case.retry_count + retry_increment,
            contact_count=case.contact_count + contact_increment,
            updated_at=occurred_at,
        )
        return self.case


def _case(
    *,
    retry_count: int,
    contact_count: int = 0,
    state: CaseState = CaseState.EXECUTING,
) -> RecoveryCase:
    return RecoveryCase(
        case_id="case_retry",
        merchant_id="merchant_retry",
        workflow_type=WorkflowType.FAILED_SUBSCRIPTION,
        subject_type=SubjectType.SUBSCRIPTION,
        subject_id="subscription_retry",
        customer_id="customer_retry",
        revenue_at_risk_minor=10_000,
        currency="INR",
        state=state,
        state_version=5,
        diagnosis="TEMPORARY_INSUFFICIENT_FUNDS",
        diagnosis_confidence=0.9,
        retry_count=retry_count,
        contact_count=contact_count,
        created_at=NOW,
        updated_at=NOW,
    )


def _action() -> SimpleNamespace:
    return SimpleNamespace(
        id="action_retry",
        merchant_id="merchant_retry",
        recovery_case_id="case_retry",
        decision_receipt_id="receipt_retry",
        action_type="DEFER_RETRY",
        target_type="SUBSCRIPTION",
        target_id="subscription_retry",
        logical_attempt=1,
        status="PENDING",
        attempt_count=1,
        parameters={"amount_minor": 10_000, "currency": "INR", "provider_mode": "TEST"},
        policy_version="phase3-conservative-default-1.0",
        correlation_id="correlation_retry",
        reconciliation_deadline=NOW,
        unknown_since=None,
        dead_lettered_at=None,
        provider_object_id="provider_retry",
    )


@pytest.mark.asyncio
async def test_retry_at_current_policy_ceiling_is_blocked_before_provider_attempt() -> None:
    actions = FakeActionRepository(_action())
    recovery = FakeRecoveryRepository(_case(retry_count=3))

    result = await ActionExecutionService(actions, recovery).prepare_execution(  # type: ignore[arg-type]
        merchant_id="merchant_retry",
        action_id="action_retry",
        lease_token="lease_retry",
        started_at=NOW,
    )

    assert isinstance(result, ExecutionDisposition)
    assert result.reason_code == "PRE_EXECUTION_RETRY_LIMIT_REACHED"
    assert result.case_state is CaseState.DECISION_PENDING
    assert actions.attempts_started == 0


@pytest.mark.asyncio
async def test_each_retry_attempt_advances_the_durable_retry_counter() -> None:
    actions = FakeActionRepository(_action())
    recovery = FakeRecoveryRepository(_case(retry_count=1))

    await ActionExecutionService(actions, recovery).prepare_execution(  # type: ignore[arg-type]
        merchant_id="merchant_retry",
        action_id="action_retry",
        lease_token="lease_retry",
        started_at=NOW,
    )

    assert actions.attempts_started == 1
    assert recovery.case.retry_count == 2


@pytest.mark.asyncio
async def test_each_contact_attempt_advances_the_durable_contact_counter() -> None:
    action = _action()
    action.action_type = "REQUEST_PAYMENT_METHOD_UPDATE"
    actions = FakeActionRepository(action)
    recovery = FakeRecoveryRepository(_case(retry_count=0, contact_count=1))
    recovery.receipt.candidate_actions = [
        {
            "action_type": "REQUEST_PAYMENT_METHOD_UPDATE",
            "recovery_probability": 0.75,
            "expected_net_recovery_minor": 7_000,
            "channel": "EMAIL",
        }
    ]

    async def granted_consent(
        **_: object,
    ) -> tuple[tuple[tuple[ContactChannel, ConsentState], ...], frozenset[ContactChannel]]:
        return ((ContactChannel.EMAIL, ConsentState.GRANTED),), frozenset()

    recovery.consent_facts = granted_consent  # type: ignore[method-assign]

    await ActionExecutionService(actions, recovery).prepare_execution(  # type: ignore[arg-type]
        merchant_id="merchant_retry",
        action_id="action_retry",
        lease_token="lease_retry",
        started_at=NOW,
    )

    assert actions.attempts_started == 1
    assert recovery.case.contact_count == 2


@pytest.mark.asyncio
async def test_claimed_retry_can_be_cancelled_before_its_next_provider_attempt() -> None:
    class FlushOnlySession:
        async def flush(self) -> None:
            return None

    action = _action()

    await ActionRepository(FlushOnlySession()).cancel_before_execution(  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        cancelled_at=NOW,
        reason_code="PRE_EXECUTION_RETRY_LIMIT_REACHED",
    )

    assert action.status == "FAILED"
    assert action.last_error_code == "PRE_EXECUTION_RETRY_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_active_incident_defers_blocked_action_with_a_durable_wakeup() -> None:
    actions = FakeActionRepository(_action())
    recovery = FakeRecoveryRepository(_case(retry_count=0, state=CaseState.READY))
    incident_ends_at = NOW.replace(hour=11)
    recovery.incidents = (
        IncidentConstraint(
            incident_id="incident_issuer",
            scope=IncidentScope.ISSUER,
            starts_at=NOW.replace(hour=9),
            ends_at=incident_ends_at,
        ),
    )

    result = await ActionExecutionService(actions, recovery).prepare_execution(  # type: ignore[arg-type]
        merchant_id="merchant_retry",
        action_id="action_retry",
        lease_token="lease_retry",
        started_at=NOW,
    )

    assert isinstance(result, ExecutionDisposition)
    assert result.reason_code == "PRE_EXECUTION_ACTIVE_INCIDENT"
    assert result.case_state is CaseState.DEFERRED
    assert recovery.case.next_evaluation_at == incident_ends_at
    assert actions.attempts_started == 0


@pytest.mark.asyncio
async def test_changed_policy_requiring_human_review_creates_a_fresh_review() -> None:
    actions = FakeActionRepository(_action())
    recovery = FakeRecoveryRepository(_case(retry_count=0, state=CaseState.READY))
    recovery.policy = replace(recovery.policy, human_review_amount_minor=1)
    recovery.reviews: list[object] = []

    async def store_review(review: object) -> None:
        recovery.reviews.append(review)

    recovery.store_review = store_review  # type: ignore[attr-defined,method-assign]

    result = await ActionExecutionService(actions, recovery).prepare_execution(  # type: ignore[arg-type]
        merchant_id="merchant_retry",
        action_id="action_retry",
        lease_token="lease_retry",
        started_at=NOW,
    )

    assert isinstance(result, ExecutionDisposition)
    assert result.reason_code == "PRE_EXECUTION_HUMAN_REVIEW_REQUIRED"
    assert result.case_state is CaseState.ESCALATED
    assert len(recovery.reviews) == 1
    assert actions.attempts_started == 0


@pytest.mark.asyncio
async def test_unverified_request_escalates_when_verification_deadline_expires() -> None:
    action = _action()
    action.status = "SUCCEEDED"
    actions = FakeActionRepository(action)
    recovery = FakeRecoveryRepository(_case(retry_count=1, state=CaseState.VERIFYING))

    result = await ActionExecutionService(actions, recovery).record_lookup(  # type: ignore[arg-type]
        merchant_id="merchant_retry",
        action_id="action_retry",
        result=ProviderLookupResult(
            status=ActionStatus.UNKNOWN,
            evidence_source=EvidenceSource.PROVIDER_LOOKUP,
            evidence_reference="provider/action_retry/pending",
            observed_at=NOW.replace(minute=1),
            is_authoritative=False,
            provider_object_id="provider_retry",
            reason_code="PROVIDER_STILL_PENDING",
        ),
    )

    assert result.action_status is ActionStatus.UNKNOWN
    assert result.case_state is CaseState.ESCALATED
    assert result.reason_code == "ESCALATED"
    assert action.status == "UNKNOWN"


@pytest.mark.asyncio
async def test_signed_payment_event_cannot_credit_a_customer_contact_action() -> None:
    action = _action()
    action.action_type = "REQUEST_PAYMENT_METHOD_UPDATE"
    action.status = "SUCCEEDED"
    actions = FakeActionRepository(action)
    recovery = FakeRecoveryRepository(_case(retry_count=0, state=CaseState.VERIFYING))
    event = RevenueRiskEvent(
        event_id="event_subscription_charged",
        merchant_id="merchant_retry",
        source=EventSource.RAZORPAY,
        source_event_id="provider_subscription_charged",
        event_type="subscription.charged",
        occurred_at=NOW,
        received_at=NOW,
        customer_id="customer_retry",
        payment_id="payment_charged",
        subscription_id="subscription_retry",
        amount_minor=10_000,
        currency="INR",
        normalized_failure_category=NormalizedFailureCategory.NONE,
        correlation_id="correlation_charged",
        source_payload_reference="webhook/provider_subscription_charged",
    )

    result = await ActionExecutionService(actions, recovery).verify_signed_event(  # type: ignore[arg-type]
        event=event,
        webhook_event_id="webhook_subscription_charged",
    )

    assert result is None
    assert actions.signed_event_action_types == (ActionType.CREATE_PAYMENT_LINK,)
    assert recovery.case.state is CaseState.VERIFYING


@pytest.mark.asyncio
async def test_already_paid_claim_freezes_outreach_pending_authoritative_verification() -> None:
    invoice = SimpleNamespace(
        customer_id="customer_receivable",
        outstanding_amount_minor=10_000,
        currency="INR",
        status="OVERDUE",
        automation_frozen_at=None,
    )
    case = SimpleNamespace(id="case_receivable")
    response = SimpleNamespace(id="response_already_paid", intent="ALREADY_PAID")

    class AlreadyPaidExtractor:
        async def extract(self, _: str) -> PromiseExtraction:
            return PromiseExtraction(
                intent=PromiseIntent.ALREADY_PAID,
                confidence_basis_points=9_500,
                extractor_version="test-extractor",
            )

    class FakePlaybookRepository:
        async def invoice(self, **_: object) -> SimpleNamespace:
            return invoice

        async def store_customer_response(self, **_: object) -> tuple[SimpleNamespace, bool]:
            return response, True

        async def store_receivable_escalation(self, **_: object) -> SimpleNamespace:
            return SimpleNamespace(id="escalation_already_paid")

        async def freeze_already_paid_claim_and_escalate(self, **_: object) -> SimpleNamespace:
            invoice.status = "ESCALATED"
            invoice.automation_frozen_at = NOW
            return SimpleNamespace(id="escalation_already_paid")

    class FakeReceivableRecovery:
        session = object()

        async def find_active_case(self, **_: object) -> SimpleNamespace:
            return case

    result = await ReceivablesPlaybookService(
        FakePlaybookRepository(),  # type: ignore[arg-type]
        FakeReceivableRecovery(),  # type: ignore[arg-type]
        extractor=AlreadyPaidExtractor(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).record_customer_response(
        merchant_id="merchant_receivable",
        source_response_id="source_already_paid",
        invoice_id="invoice_receivable",
        body="We already paid this invoice.",
    )

    assert result.disposition == "AUTHORITATIVE_VERIFICATION_REQUIRED"
    assert invoice.status == "ESCALATED"
    assert invoice.automation_frozen_at == NOW
