"""Transactional persistence for recovery cases, policy, and decision evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any, cast

from revenueguard_domain import (
    ActionType,
    CaseState,
    CaseTransition,
    ConsentState,
    ContactChannel,
    HumanReviewRequest,
    IncidentConstraint,
    IncidentScope,
    MerchantPolicySnapshot,
    ModelPrediction,
    ReviewStatus,
    SubjectType,
    WorkflowType,
)
from revenueguard_domain import DecisionReceipt as DomainDecisionReceipt
from revenueguard_domain import RecoveryCase as DomainRecoveryCase
from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from revenueguard_integrations.persistence.models import (
    CaseTransition as CaseTransitionRow,
)
from revenueguard_integrations.persistence.models import (
    CommunicationConsent,
    DecisionReceipt,
    HumanReview,
    Merchant,
    MerchantPolicyVersion,
    NormalizedEvent,
    Payment,
    PortfolioIncident,
    RecoveryCase,
    RecoveryCaseEvent,
    Subscription,
)
from revenueguard_integrations.persistence.models import (
    ModelPrediction as ModelPredictionRow,
)
from revenueguard_integrations.persistence.status_ordering import compare_provider_status


class EvidenceDisposition(StrEnum):
    APPLIED = "APPLIED"
    IGNORED_STALE = "IGNORED_STALE"
    AUDIT_ONLY = "AUDIT_ONLY"


class RecoveryPersistenceError(RuntimeError):
    """Base error for recovery persistence failures."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class MissingPolicyError(RecoveryPersistenceError):
    """No immutable policy is effective at the requested time."""


class StaleRecoveryCaseError(RecoveryPersistenceError):
    """An optimistic case update lost a concurrency race."""


@dataclass(frozen=True, slots=True)
class EvidenceLinkResult:
    link: RecoveryCaseEvent
    created: bool


@dataclass(frozen=True, slots=True)
class AuthoritativeFacts:
    provider_updated_at: datetime | None
    status: str | None


@dataclass(frozen=True, slots=True)
class EvidenceOrder:
    disposition: EvidenceDisposition
    reason_code: str


class RecoveryRepository:
    """Keep recovery writes inside a caller-owned SQLAlchemy transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expose the caller-owned transaction to closely related repositories."""

        return self._session

    async def publish_policy(
        self,
        *,
        merchant_id: str,
        policy: MerchantPolicySnapshot,
        published_by: str,
    ) -> MerchantPolicyVersion:
        if not published_by:
            raise ValueError("published_by is required")
        existing = await self._session.get(MerchantPolicyVersion, (merchant_id, policy.version))
        if existing is not None:
            if existing.content_sha256 != policy.content_digest:
                raise RecoveryPersistenceError(
                    "POLICY_VERSION_CONTENT_MISMATCH",
                    "policy version already exists with different content",
                )
            return existing
        effective_collision = (
            await self._session.scalars(
                select(MerchantPolicyVersion).where(
                    MerchantPolicyVersion.merchant_id == merchant_id,
                    MerchantPolicyVersion.effective_at == policy.effective_at,
                )
            )
        ).one_or_none()
        if effective_collision is not None:
            raise RecoveryPersistenceError(
                "POLICY_EFFECTIVE_TIME_CONFLICT",
                "another policy version has the same effective timestamp",
            )
        row = MerchantPolicyVersion(
            merchant_id=merchant_id,
            version=policy.version,
            snapshot=policy.canonical_document(),
            content_sha256=policy.content_digest,
            published_by=published_by,
            effective_at=policy.effective_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def effective_policy(
        self, *, merchant_id: str, evaluated_at: datetime
    ) -> MerchantPolicySnapshot:
        row = (
            await self._session.scalars(
                select(MerchantPolicyVersion)
                .where(
                    MerchantPolicyVersion.merchant_id == merchant_id,
                    MerchantPolicyVersion.effective_at <= evaluated_at,
                )
                .order_by(MerchantPolicyVersion.effective_at.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            raise MissingPolicyError(
                "MISSING_EFFECTIVE_POLICY",
                f"no policy is effective for merchant {merchant_id}",
            )
        policy = _policy_from_document(row.snapshot)
        if policy.content_digest != row.content_sha256:
            raise RecoveryPersistenceError(
                "POLICY_DIGEST_MISMATCH", "stored policy content does not match its digest"
            )
        return policy

    async def get_case(
        self,
        *,
        merchant_id: str,
        case_id: str,
        for_update: bool = False,
    ) -> DomainRecoveryCase | None:
        row = await self._case_row(
            merchant_id=merchant_id,
            case_id=case_id,
            for_update=for_update,
        )
        return _case_from_row(row) if row is not None else None

    async def lock_merchant(self, *, merchant_id: str) -> None:
        merchant = (
            await self._session.scalars(
                select(Merchant).where(Merchant.id == merchant_id).with_for_update()
            )
        ).one_or_none()
        if merchant is None:
            raise LookupError("merchant does not exist")

    async def get_normalized_event(
        self, *, merchant_id: str, normalized_event_id: str
    ) -> NormalizedEvent | None:
        return (
            await self._session.scalars(
                select(NormalizedEvent).where(
                    NormalizedEvent.merchant_id == merchant_id,
                    NormalizedEvent.id == normalized_event_id,
                )
            )
        ).one_or_none()

    async def get_case_record(
        self, *, merchant_id: str, case_id: str, for_update: bool = False
    ) -> RecoveryCase | None:
        return await self._case_row(
            merchant_id=merchant_id,
            case_id=case_id,
            for_update=for_update,
        )

    async def find_active_case(
        self,
        *,
        merchant_id: str,
        workflow_type: str,
        subject_type: str,
        subject_id: str,
        for_update: bool = False,
    ) -> RecoveryCase | None:
        statement = select(RecoveryCase).where(
            RecoveryCase.merchant_id == merchant_id,
            RecoveryCase.workflow_type == workflow_type,
            RecoveryCase.subject_type == subject_type,
            RecoveryCase.subject_id == subject_id,
            RecoveryCase.state.not_in((CaseState.RECOVERED.value, CaseState.STOPPED.value)),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def find_episode_case(
        self,
        *,
        merchant_id: str,
        workflow_type: str,
        subject_type: str,
        subject_id: str,
        recovery_episode_key: str,
    ) -> RecoveryCase | None:
        return (
            await self._session.scalars(
                select(RecoveryCase).where(
                    RecoveryCase.merchant_id == merchant_id,
                    RecoveryCase.workflow_type == workflow_type,
                    RecoveryCase.subject_type == subject_type,
                    RecoveryCase.subject_id == subject_id,
                    RecoveryCase.recovery_episode_key == recovery_episode_key,
                )
            )
        ).one_or_none()

    async def create_case(
        self,
        case: DomainRecoveryCase,
        *,
        recovery_episode_key: str | None,
        latest_evidence_event_id: str,
        latest_evidence_occurred_at: datetime,
        diagnosis_confidence_basis_points: int | None = None,
    ) -> RecoveryCase:
        row = RecoveryCase(
            merchant_id=case.merchant_id,
            id=case.case_id,
            schema_version=case.schema_version,
            workflow_type=case.workflow_type.value,
            subject_type=case.subject_type.value,
            subject_id=case.subject_id,
            customer_id=case.customer_id,
            revenue_at_risk_minor=case.revenue_at_risk_minor,
            currency=case.currency,
            state=case.state.value,
            state_version=case.state_version,
            diagnosis=case.diagnosis,
            diagnosis_confidence_basis_points=diagnosis_confidence_basis_points,
            retry_count=case.retry_count,
            contact_count=case.contact_count,
            active_incident_id=case.active_incident_id,
            next_evaluation_at=case.next_evaluation_at,
            terminal_reason=case.terminal_reason,
            recovery_episode_key=recovery_episode_key,
            latest_evidence_event_id=latest_evidence_event_id,
            latest_evidence_occurred_at=latest_evidence_occurred_at,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def link_evidence(
        self,
        *,
        merchant_id: str,
        normalized_event_id: str,
        recovery_case_id: str | None,
        disposition: EvidenceDisposition,
        reason_code: str,
    ) -> EvidenceLinkResult:
        statement = (
            insert(RecoveryCaseEvent)
            .values(
                merchant_id=merchant_id,
                normalized_event_id=normalized_event_id,
                recovery_case_id=recovery_case_id,
                disposition=disposition.value,
                reason_code=reason_code,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    RecoveryCaseEvent.merchant_id,
                    RecoveryCaseEvent.normalized_event_id,
                ]
            )
            .returning(RecoveryCaseEvent)
        )
        created = (await self._session.scalars(statement)).one_or_none()
        if created is not None:
            return EvidenceLinkResult(created, True)
        existing = (
            await self._session.scalars(
                select(RecoveryCaseEvent).where(
                    RecoveryCaseEvent.merchant_id == merchant_id,
                    RecoveryCaseEvent.normalized_event_id == normalized_event_id,
                )
            )
        ).one()
        return EvidenceLinkResult(existing, False)

    async def apply_transition(
        self,
        *,
        updated_case: DomainRecoveryCase,
        transition: CaseTransition,
        latest_evidence_event_id: str | None = None,
        latest_evidence_occurred_at: datetime | None = None,
    ) -> RecoveryCase:
        values: dict[str, Any] = {
            "state": updated_case.state.value,
            "state_version": updated_case.state_version,
            "updated_at": updated_case.updated_at,
            "next_evaluation_at": updated_case.next_evaluation_at,
            "terminal_reason": updated_case.terminal_reason,
            "diagnosis": updated_case.diagnosis,
            "diagnosis_confidence_basis_points": (
                round(updated_case.diagnosis_confidence * 10_000)
                if updated_case.diagnosis_confidence is not None
                else None
            ),
            "retry_count": updated_case.retry_count,
            "contact_count": updated_case.contact_count,
            "active_incident_id": updated_case.active_incident_id,
        }
        if latest_evidence_event_id is not None:
            values["latest_evidence_event_id"] = latest_evidence_event_id
            values["latest_evidence_occurred_at"] = latest_evidence_occurred_at
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(RecoveryCase)
                .where(
                    RecoveryCase.merchant_id == transition.merchant_id,
                    RecoveryCase.id == transition.case_id,
                    RecoveryCase.state == transition.before_state.value,
                    RecoveryCase.state_version == transition.before_version,
                )
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise StaleRecoveryCaseError(
                "STALE_CASE_VERSION",
                "case state or version changed before the transition was persisted",
            )
        self._session.add(
            CaseTransitionRow(
                merchant_id=transition.merchant_id,
                recovery_case_id=transition.case_id,
                before_state=transition.before_state.value,
                after_state=transition.after_state.value,
                before_version=transition.before_version,
                after_version=transition.after_version,
                actor=transition.actor,
                reason_code=transition.reason_code,
                reason_detail=transition.reason_detail,
                correlation_id=transition.correlation_id,
                policy_version=transition.policy_version,
                authoritative_evidence_reference=(transition.authoritative_evidence_reference),
                occurred_at=transition.occurred_at,
            )
        )
        await self._session.flush()
        return (
            await self._session.scalars(
                select(RecoveryCase).where(
                    RecoveryCase.merchant_id == transition.merchant_id,
                    RecoveryCase.id == transition.case_id,
                )
            )
        ).one()

    async def get_review(
        self,
        *,
        merchant_id: str,
        review_id: str,
        for_update: bool = False,
    ) -> HumanReviewRequest | None:
        statement = select(HumanReview).where(
            HumanReview.merchant_id == merchant_id,
            HumanReview.id == review_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.scalars(statement)).one_or_none()
        return _review_from_row(row) if row is not None else None

    async def get_decision_receipt(
        self, *, merchant_id: str, receipt_id: str
    ) -> DecisionReceipt | None:
        return (
            await self._session.scalars(
                select(DecisionReceipt).where(
                    DecisionReceipt.merchant_id == merchant_id,
                    DecisionReceipt.id == receipt_id,
                )
            )
        ).one_or_none()

    async def store_review(self, review: HumanReviewRequest) -> HumanReview:
        row = HumanReview(
            merchant_id=review.merchant_id,
            id=review.review_id,
            recovery_case_id=review.case_id,
            action_fingerprint=review.action_fingerprint,
            proposed_action_type=review.proposed_action_type,
            evidence_references=list(review.evidence_references),
            policy_version=review.policy_version,
            policy_digest=review.policy_digest,
            reason_code=review.reason_code,
            risk_detail=review.risk_detail,
            requested_at=review.requested_at,
            expires_at=review.expires_at,
            status=review.status.value,
            reviewer_id=review.reviewer_id,
            rationale=review.rationale,
            decided_at=review.decided_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_review_decision(
        self,
        *,
        merchant_id: str,
        review: HumanReviewRequest,
    ) -> HumanReview:
        if review.status is ReviewStatus.REQUESTED:
            raise ValueError("review decision must be terminal")
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(HumanReview)
                .where(
                    HumanReview.merchant_id == merchant_id,
                    HumanReview.id == review.review_id,
                    HumanReview.status == ReviewStatus.REQUESTED.value,
                )
                .values(
                    status=review.status.value,
                    reviewer_id=review.reviewer_id,
                    rationale=review.rationale,
                    decided_at=review.decided_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise RecoveryPersistenceError(
                "REVIEW_ALREADY_DECIDED", "review is missing or no longer requested"
            )
        return (
            await self._session.scalars(
                select(HumanReview).where(
                    HumanReview.merchant_id == merchant_id,
                    HumanReview.id == review.review_id,
                )
            )
        ).one()

    async def store_receipt(self, receipt: DomainDecisionReceipt) -> DecisionReceipt:
        row = DecisionReceipt(
            merchant_id=receipt.merchant_id,
            id=receipt.receipt_id,
            recovery_case_id=receipt.case_id,
            correlation_id=receipt.correlation_id,
            evidence_references=list(receipt.evidence_references),
            candidate_actions=[candidate.to_dict() for candidate in receipt.candidate_actions],
            selected_action_type=receipt.selected_action_type.value,
            explanation=receipt.explanation,
            policy_result=receipt.policy_result.value,
            policy_reason_codes=list(receipt.policy_reason_codes),
            policy_version=receipt.versions.policy,
            version_bundle=receipt.versions.to_dict(),
            human_review_id=receipt.human_review_id,
            resulting_action_id=receipt.resulting_action_id,
            resulting_state=receipt.resulting_state.value,
            audit_entry_id=receipt.audit_entry_id,
            model_prediction_ids=list(receipt.model_prediction_ids),
            schema_version=receipt.schema_version,
            created_at=receipt.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def store_model_predictions(
        self, predictions: tuple[ModelPrediction, ...]
    ) -> tuple[ModelPredictionRow, ...]:
        rows: list[ModelPredictionRow] = []
        for prediction in predictions:
            existing = await self._session.get(
                ModelPredictionRow,
                (prediction.merchant_id, prediction.prediction_id),
            )
            if existing is not None:
                if (
                    existing.run_id != prediction.run_id
                    or existing.node != prediction.node.value
                    or existing.input_sha256 != prediction.input_sha256
                ):
                    raise RecoveryPersistenceError(
                        "MODEL_PREDICTION_ID_CONFLICT",
                        "prediction ID already identifies different model evidence",
                    )
                rows.append(existing)
                continue
            row = ModelPredictionRow(
                merchant_id=prediction.merchant_id,
                id=prediction.prediction_id,
                run_id=prediction.run_id,
                recovery_case_id=prediction.case_id,
                correlation_id=prediction.correlation_id,
                node=prediction.node.value,
                status=prediction.status.value,
                input_sha256=prediction.input_sha256,
                output_payload=dict(prediction.output_payload),
                model_version=prediction.model_version,
                prompt_version=prediction.prompt_version,
                schema_version=prediction.schema_version,
                feature_version=prediction.feature_version,
                latency_ms=prediction.latency_ms,
                input_tokens=prediction.input_tokens,
                output_tokens=prediction.output_tokens,
                failure_code=prediction.failure_code,
                created_at=prediction.created_at,
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        return tuple(rows)

    async def due_deferred_cases(self, *, due_at: datetime, limit: int) -> list[RecoveryCase]:
        statement: Select[tuple[RecoveryCase]] = (
            select(RecoveryCase)
            .where(
                RecoveryCase.state == CaseState.DEFERRED.value,
                RecoveryCase.next_evaluation_at.is_not(None),
                RecoveryCase.next_evaluation_at <= due_at,
            )
            .order_by(RecoveryCase.next_evaluation_at, RecoveryCase.merchant_id, RecoveryCase.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def consent_facts(
        self, *, merchant_id: str, customer_id: str | None
    ) -> tuple[
        tuple[tuple[ContactChannel, ConsentState], ...],
        frozenset[ContactChannel],
    ]:
        if customer_id is None:
            return (), frozenset()
        rows = list(
            (
                await self._session.scalars(
                    select(CommunicationConsent).where(
                        CommunicationConsent.merchant_id == merchant_id,
                        CommunicationConsent.customer_id == customer_id,
                    )
                )
            ).all()
        )
        consent = tuple(
            (ContactChannel(row.channel), ConsentState(row.state))
            for row in sorted(rows, key=lambda item: item.channel)
        )
        opted_out = frozenset(ContactChannel(row.channel) for row in rows if row.opted_out)
        return consent, opted_out

    async def active_incidents(
        self, *, merchant_id: str, evaluated_at: datetime
    ) -> tuple[IncidentConstraint, ...]:
        rows = list(
            (
                await self._session.scalars(
                    select(PortfolioIncident).where(
                        PortfolioIncident.merchant_id == merchant_id,
                        PortfolioIncident.status == "ACTIVE",
                        PortfolioIncident.starts_at <= evaluated_at,
                        PortfolioIncident.ends_at > evaluated_at,
                    )
                )
            ).all()
        )
        return tuple(
            IncidentConstraint(
                incident_id=row.id,
                scope=IncidentScope(row.scope),
                channel=ContactChannel(row.channel) if row.channel else None,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            )
            for row in sorted(rows, key=lambda item: item.id)
        )

    async def authoritative_facts(self, event: NormalizedEvent) -> AuthoritativeFacts:
        if event.subscription_id is not None:
            return await self._subscription_facts(
                merchant_id=event.merchant_id,
                subscription_id=event.subscription_id,
            )
        if event.payment_id is not None:
            return await self._payment_facts(
                merchant_id=event.merchant_id,
                payment_id=event.payment_id,
            )
        return AuthoritativeFacts(None, None)

    async def authoritative_facts_for_case(
        self,
        *,
        merchant_id: str,
        case: DomainRecoveryCase,
    ) -> AuthoritativeFacts:
        if case.merchant_id != merchant_id:
            raise ValueError("case does not belong to the requested merchant")
        if case.subject_type is SubjectType.SUBSCRIPTION:
            return await self._subscription_facts(
                merchant_id=merchant_id,
                subscription_id=case.subject_id,
            )
        if case.subject_type is SubjectType.PAYMENT:
            return await self._payment_facts(
                merchant_id=merchant_id,
                payment_id=case.subject_id,
            )
        return AuthoritativeFacts(None, None)

    async def _case_row(
        self,
        *,
        merchant_id: str,
        case_id: str,
        for_update: bool,
    ) -> RecoveryCase | None:
        statement = select(RecoveryCase).where(
            RecoveryCase.merchant_id == merchant_id,
            RecoveryCase.id == case_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def _payment_facts(
        self,
        *,
        merchant_id: str,
        payment_id: str,
    ) -> AuthoritativeFacts:
        row = await self._session.get(Payment, (merchant_id, payment_id))
        return _provider_facts(row)

    async def _subscription_facts(
        self,
        *,
        merchant_id: str,
        subscription_id: str,
    ) -> AuthoritativeFacts:
        row = await self._session.get(Subscription, (merchant_id, subscription_id))
        return _provider_facts(row)


def order_evidence(
    *,
    event_occurred_at: datetime,
    event_id: str,
    event_status: str | None,
    authoritative: AuthoritativeFacts,
    case_watermark_at: datetime | None,
    case_watermark_event_id: str | None,
    case_status: str | None = None,
) -> EvidenceOrder:
    """Apply timestamps, status truth, then equivalent-evidence IDs in that order."""

    event_time = _utc(event_occurred_at)
    authoritative_time = (
        _utc(authoritative.provider_updated_at)
        if authoritative.provider_updated_at is not None
        else None
    )
    watermark = _utc(case_watermark_at) if case_watermark_at is not None else None
    if authoritative_time is not None and event_time < authoritative_time:
        return EvidenceOrder(EvidenceDisposition.IGNORED_STALE, "OLDER_THAN_PROVIDER_TRUTH")
    if watermark is not None and event_time < watermark:
        return EvidenceOrder(EvidenceDisposition.IGNORED_STALE, "OLDER_THAN_CASE_WATERMARK")

    if authoritative_time is not None and event_time == authoritative_time:
        comparison = compare_provider_status(event_status, authoritative.status)
        if comparison < 0:
            return EvidenceOrder(
                EvidenceDisposition.IGNORED_STALE, "LOWER_PRECEDENCE_PROVIDER_TRUTH"
            )
    if watermark is not None and event_time == watermark:
        comparison = compare_provider_status(event_status, case_status)
        if comparison < 0:
            return EvidenceOrder(EvidenceDisposition.IGNORED_STALE, "LOWER_PRECEDENCE_CASE_TRUTH")
        if comparison == 0 and case_watermark_event_id is not None:
            if event_id <= case_watermark_event_id:
                return EvidenceOrder(
                    EvidenceDisposition.IGNORED_STALE, "EQUIVALENT_EVIDENCE_TIE_BREAK"
                )
    return EvidenceOrder(EvidenceDisposition.APPLIED, "EVIDENCE_ACCEPTED")


def _case_from_row(row: RecoveryCase) -> DomainRecoveryCase:
    confidence = row.diagnosis_confidence_basis_points
    return DomainRecoveryCase(
        case_id=row.id,
        merchant_id=row.merchant_id,
        workflow_type=WorkflowType(row.workflow_type),
        subject_type=SubjectType(row.subject_type),
        subject_id=row.subject_id,
        customer_id=row.customer_id,
        revenue_at_risk_minor=row.revenue_at_risk_minor,
        currency=row.currency,
        state=CaseState(row.state),
        state_version=row.state_version,
        diagnosis=row.diagnosis,
        diagnosis_confidence=confidence / 10_000 if confidence is not None else None,
        retry_count=row.retry_count,
        contact_count=row.contact_count,
        active_incident_id=row.active_incident_id,
        next_evaluation_at=row.next_evaluation_at,
        terminal_reason=row.terminal_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
        schema_version=row.schema_version,
    )


def _review_from_row(row: HumanReview) -> HumanReviewRequest:
    return HumanReviewRequest(
        review_id=row.id,
        merchant_id=row.merchant_id,
        case_id=row.recovery_case_id,
        action_fingerprint=row.action_fingerprint,
        proposed_action_type=row.proposed_action_type,
        evidence_references=tuple(row.evidence_references),
        policy_version=row.policy_version,
        policy_digest=row.policy_digest,
        reason_code=row.reason_code,
        risk_detail=row.risk_detail,
        requested_at=row.requested_at,
        expires_at=row.expires_at,
        status=ReviewStatus(row.status),
        reviewer_id=row.reviewer_id,
        rationale=row.rationale,
        decided_at=row.decided_at,
    )


def _provider_facts(row: Payment | Subscription | None) -> AuthoritativeFacts:
    if row is None:
        return AuthoritativeFacts(None, None)
    return AuthoritativeFacts(
        row.provider_updated_at or row.provider_occurred_at,
        row.status,
    )


def _policy_from_document(document: dict[str, Any]) -> MerchantPolicySnapshot:
    return MerchantPolicySnapshot(
        version=str(document["version"]),
        effective_at=_parse_datetime(document["effective_at"]),
        allowed_actions=frozenset(ActionType(value) for value in document["allowed_actions"]),
        retry_limit=int(document["retry_limit"]),
        contact_limit=int(document["contact_limit"]),
        minimum_expected_net_recovery_minor=int(document["minimum_expected_net_recovery_minor"]),
        human_review_amount_minor=int(document["human_review_amount_minor"]),
        minimum_confidence_basis_points=int(document["minimum_confidence_basis_points"]),
        default_defer_seconds=int(document["default_defer_seconds"]),
        timezone=str(document["timezone"]),
        quiet_hours_start=time.fromisoformat(str(document["quiet_hours_start"])),
        quiet_hours_end=time.fromisoformat(str(document["quiet_hours_end"])),
        currency=str(document["currency"]),
        features_version=str(document["features_version"]),
    )


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("policy effective_at must be an ISO-8601 string")
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
