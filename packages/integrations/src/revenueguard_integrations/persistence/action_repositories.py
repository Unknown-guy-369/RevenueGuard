"""Transactional action-outbox, attempt, and verified-outcome persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from revenueguard_domain import (
    ActionStatus,
    ActionType,
    SubjectType,
)
from revenueguard_domain import (
    RecoveryAction as DomainRecoveryAction,
)
from revenueguard_domain import VerifiedOutcome as DomainVerifiedOutcome
from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from revenueguard_integrations.persistence.models import (
    ActionAttempt,
    RecoveryAction,
    RecoveryCase,
    VerifiedOutcome,
)


class ActionPersistenceError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class ClaimedAction:
    merchant_id: str
    action_id: str
    lease_token: str


@dataclass(frozen=True, slots=True)
class RecoveryTotals:
    merchant_id: str
    currency: str
    recovered_amount_minor: int
    verified_action_count: int


class ActionRepository:
    """Keep effect-related writes inside the caller-owned database transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def store_action(
        self,
        action: DomainRecoveryAction,
        *,
        policy_version: str,
        correlation_id: str,
        max_attempts: int,
        reconciliation_deadline: datetime,
    ) -> RecoveryAction:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if reconciliation_deadline <= action.authorized_at:
            raise ValueError("reconciliation deadline must follow authorization")
        existing = (
            await self._session.scalars(
                select(RecoveryAction).where(
                    RecoveryAction.idempotency_key == action.idempotency_key
                )
            )
        ).one_or_none()
        if existing is not None:
            if existing.merchant_id != action.merchant_id or existing.id != action.action_id:
                raise ActionPersistenceError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "idempotency key already identifies another logical action",
                )
            return existing
        row = RecoveryAction(
            merchant_id=action.merchant_id,
            id=action.action_id,
            recovery_case_id=action.case_id,
            decision_receipt_id=action.decision_receipt_id,
            action_type=action.action_type.value,
            target_type=action.target_type.value,
            target_id=action.target_id,
            logical_attempt=action.logical_attempt,
            idempotency_key=action.idempotency_key,
            status=action.status.value,
            parameters=dict(action.parameters),
            policy_version=policy_version,
            correlation_id=correlation_id,
            authorized_at=action.authorized_at,
            execute_after=action.execute_after,
            next_attempt_at=action.execute_after,
            attempt_count=0,
            max_attempts=max_attempts,
            reconciliation_deadline=reconciliation_deadline,
            schema_version=action.schema_version,
            created_at=action.created_at,
            updated_at=action.created_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_action(
        self,
        *,
        merchant_id: str,
        action_id: str,
        for_update: bool = False,
    ) -> RecoveryAction | None:
        statement = select(RecoveryAction).where(
            RecoveryAction.merchant_id == merchant_id,
            RecoveryAction.id == action_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def domain_action(
        self, *, merchant_id: str, action_id: str, for_update: bool = False
    ) -> DomainRecoveryAction | None:
        row = await self.get_action(
            merchant_id=merchant_id,
            action_id=action_id,
            for_update=for_update,
        )
        return _action_from_row(row) if row is not None else None

    async def has_unknown_equivalent(
        self,
        *,
        merchant_id: str,
        recovery_case_id: str,
        action_type: ActionType,
        target_id: str,
    ) -> bool:
        value = await self._session.scalar(
            select(
                exists().where(
                    RecoveryAction.merchant_id == merchant_id,
                    RecoveryAction.recovery_case_id == recovery_case_id,
                    RecoveryAction.action_type == action_type.value,
                    RecoveryAction.target_id == target_id,
                    RecoveryAction.status == ActionStatus.UNKNOWN.value,
                )
            )
        )
        return bool(value)

    async def claim_due_actions(
        self, *, now: datetime, lease_for: timedelta, limit: int
    ) -> tuple[ClaimedAction, ...]:
        if lease_for <= timedelta(0) or limit <= 0:
            raise ValueError("lease_for and limit must be positive")
        open_attempt = exists().where(
            ActionAttempt.merchant_id == RecoveryAction.merchant_id,
            ActionAttempt.recovery_action_id == RecoveryAction.id,
            ActionAttempt.completed_at.is_(None),
        )
        statement: Select[tuple[RecoveryAction]] = (
            select(RecoveryAction)
            .where(
                RecoveryAction.status == ActionStatus.PENDING.value,
                RecoveryAction.dead_lettered_at.is_(None),
                RecoveryAction.execute_after <= now,
                RecoveryAction.next_attempt_at <= now,
                RecoveryAction.attempt_count < RecoveryAction.max_attempts,
                or_(
                    RecoveryAction.lease_token.is_(None),
                    RecoveryAction.lease_expires_at <= now,
                ),
                ~open_attempt,
            )
            .order_by(
                RecoveryAction.next_attempt_at,
                RecoveryAction.merchant_id,
                RecoveryAction.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self._session.scalars(statement)).all())
        claims: list[ClaimedAction] = []
        for row in rows:
            lease_token = uuid4().hex
            row.lease_token = lease_token
            row.lease_expires_at = now + lease_for
            row.updated_at = now
            claims.append(ClaimedAction(row.merchant_id, row.id, lease_token))
        await self._session.flush()
        return tuple(claims)

    async def begin_attempt(
        self,
        *,
        merchant_id: str,
        action_id: str,
        lease_token: str,
        started_at: datetime,
    ) -> tuple[RecoveryAction, ActionAttempt]:
        action = await self.get_action(
            merchant_id=merchant_id,
            action_id=action_id,
            for_update=True,
        )
        if action is None:
            raise LookupError("tenant-scoped recovery action does not exist")
        if action.status != ActionStatus.PENDING.value:
            raise ActionPersistenceError("ACTION_NOT_PENDING", "action cannot begin execution")
        if action.lease_token != lease_token or action.lease_expires_at is None:
            raise ActionPersistenceError("STALE_ACTION_LEASE", "action lease is no longer owned")
        if action.lease_expires_at <= started_at:
            raise ActionPersistenceError("EXPIRED_ACTION_LEASE", "action lease expired")
        open_attempt = (
            await self._session.scalars(
                select(ActionAttempt).where(
                    ActionAttempt.merchant_id == merchant_id,
                    ActionAttempt.recovery_action_id == action_id,
                    ActionAttempt.completed_at.is_(None),
                )
            )
        ).one_or_none()
        if open_attempt is not None:
            raise ActionPersistenceError(
                "ACTION_ATTEMPT_IN_FLIGHT", "an incomplete call must reconcile before retry"
            )
        action.attempt_count += 1
        request_material = f"{action.idempotency_key}:{action.attempt_count}"
        request_id = f"req_{sha256(request_material.encode()).hexdigest()}"
        attempt = ActionAttempt(
            id=f"attempt_{sha256((request_material + ':row').encode()).hexdigest()[:32]}",
            merchant_id=merchant_id,
            recovery_action_id=action_id,
            attempt_number=action.attempt_count,
            request_id=request_id,
            lease_token=lease_token,
            started_at=started_at,
        )
        self._session.add(attempt)
        action.updated_at = started_at
        await self._session.flush()
        return action, attempt

    async def finish_attempt(
        self,
        *,
        merchant_id: str,
        action_id: str,
        lease_token: str,
        completed_at: datetime,
        outcome_status: ActionStatus,
        response_category: str,
        provider_object_id: str | None,
        provider_status_code: int | None,
        error_code: str | None,
        response_reference: str | None,
        retryable: bool,
        retry_at: datetime | None,
        reconciliation_deadline: datetime,
    ) -> tuple[RecoveryAction, ActionAttempt]:
        action = await self.get_action(
            merchant_id=merchant_id,
            action_id=action_id,
            for_update=True,
        )
        if action is None:
            raise LookupError("tenant-scoped recovery action does not exist")
        attempt = (
            await self._session.scalars(
                select(ActionAttempt).where(
                    ActionAttempt.merchant_id == merchant_id,
                    ActionAttempt.recovery_action_id == action_id,
                    ActionAttempt.lease_token == lease_token,
                    ActionAttempt.completed_at.is_(None),
                )
            )
        ).one_or_none()
        if attempt is None:
            raise ActionPersistenceError("ACTION_ATTEMPT_NOT_OPEN", "attempt is already complete")
        attempt.completed_at = completed_at
        attempt.outcome_status = outcome_status.value
        attempt.response_category = response_category
        attempt.provider_object_id = provider_object_id
        attempt.provider_status_code = provider_status_code
        attempt.error_code = error_code
        attempt.response_reference = response_reference
        attempt.retryable = retryable
        action.provider_object_id = provider_object_id or action.provider_object_id
        action.last_error_code = error_code
        action.lease_token = None
        action.lease_expires_at = None
        action.updated_at = completed_at
        if outcome_status is ActionStatus.FAILED and retryable and retry_at is not None:
            if action.attempt_count < action.max_attempts:
                action.next_attempt_at = retry_at
                await self._session.flush()
                return action, attempt
        action.status = outcome_status.value
        if outcome_status is ActionStatus.UNKNOWN:
            action.unknown_since = completed_at
            action.reconciliation_deadline = reconciliation_deadline
        else:
            action.unknown_since = None
        if outcome_status is ActionStatus.FAILED:
            action.dead_lettered_at = completed_at
        await self._session.flush()
        return action, attempt

    async def stale_inflight_actions(self, *, now: datetime, limit: int) -> list[RecoveryAction]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        statement = (
            select(RecoveryAction)
            .join(
                ActionAttempt,
                (ActionAttempt.merchant_id == RecoveryAction.merchant_id)
                & (ActionAttempt.recovery_action_id == RecoveryAction.id),
            )
            .where(
                RecoveryAction.status == ActionStatus.PENDING.value,
                RecoveryAction.lease_expires_at <= now,
                ActionAttempt.completed_at.is_(None),
            )
            .order_by(ActionAttempt.started_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).unique().all())

    async def mark_stale_inflight_unknown(
        self,
        *,
        action: RecoveryAction,
        now: datetime,
        reconciliation_deadline: datetime,
    ) -> ActionAttempt:
        attempt = (
            await self._session.scalars(
                select(ActionAttempt).where(
                    ActionAttempt.merchant_id == action.merchant_id,
                    ActionAttempt.recovery_action_id == action.id,
                    ActionAttempt.completed_at.is_(None),
                )
            )
        ).one()
        attempt.completed_at = now
        attempt.outcome_status = ActionStatus.UNKNOWN.value
        attempt.response_category = "WORKER_LOST_DURING_PROVIDER_CALL"
        attempt.error_code = "INCOMPLETE_PROVIDER_CALL"
        action.status = ActionStatus.UNKNOWN.value
        action.unknown_since = now
        action.reconciliation_deadline = reconciliation_deadline
        action.lease_token = None
        action.lease_expires_at = None
        action.last_error_code = "INCOMPLETE_PROVIDER_CALL"
        action.updated_at = now
        await self._session.flush()
        return attempt

    async def mark_verification_expired_unknown(
        self,
        *,
        action: RecoveryAction,
        observed_at: datetime,
    ) -> None:
        if action.status != ActionStatus.SUCCEEDED.value:
            raise ActionPersistenceError(
                "ACTION_NOT_VERIFYING",
                "only an accepted action awaiting verification can expire to unknown",
            )
        action.status = ActionStatus.UNKNOWN.value
        action.unknown_since = observed_at
        action.lease_token = None
        action.lease_expires_at = None
        action.last_error_code = "VERIFICATION_DEADLINE_EXCEEDED"
        action.updated_at = observed_at
        await self._session.flush()

    async def store_outcome(self, outcome: DomainVerifiedOutcome) -> VerifiedOutcome:
        existing = await self._session.get(
            VerifiedOutcome, (outcome.merchant_id, outcome.outcome_id)
        )
        if existing is not None:
            return existing
        row = VerifiedOutcome(
            merchant_id=outcome.merchant_id,
            id=outcome.outcome_id,
            recovery_action_id=outcome.action_id,
            recovery_case_id=outcome.case_id,
            outcome_status=outcome.outcome_status.value,
            is_authoritative=outcome.is_authoritative,
            evidence_source=outcome.evidence_source.value,
            evidence_reference=outcome.evidence_reference,
            provider_object_id=outcome.provider_object_id,
            recovered_amount_minor=outcome.recovered_amount_minor,
            currency=outcome.currency,
            reason_code=outcome.reason_code,
            observed_at=outcome.observed_at,
            verified_at=outcome.verified_at,
            created_at=outcome.created_at,
            schema_version=outcome.schema_version,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def find_action_for_provider_object(
        self,
        *,
        merchant_id: str,
        provider_object_id: str,
        action_types: tuple[ActionType, ...],
        for_update: bool = False,
    ) -> RecoveryAction | None:
        if not action_types:
            raise ValueError("action_types cannot be empty")
        statement = select(RecoveryAction).where(
            RecoveryAction.merchant_id == merchant_id,
            RecoveryAction.provider_object_id == provider_object_id,
            RecoveryAction.action_type.in_(tuple(item.value for item in action_types)),
            RecoveryAction.status.in_((ActionStatus.SUCCEEDED.value, ActionStatus.UNKNOWN.value)),
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def find_latest_action_for_target(
        self,
        *,
        merchant_id: str,
        target_id: str,
        action_types: tuple[ActionType, ...],
        for_update: bool = False,
    ) -> RecoveryAction | None:
        if not action_types:
            raise ValueError("action_types cannot be empty")
        statement = (
            select(RecoveryAction)
            .where(
                RecoveryAction.merchant_id == merchant_id,
                RecoveryAction.target_id == target_id,
                RecoveryAction.action_type.in_(tuple(item.value for item in action_types)),
                RecoveryAction.status.in_(
                    (ActionStatus.SUCCEEDED.value, ActionStatus.UNKNOWN.value)
                ),
            )
            .order_by(RecoveryAction.authorized_at.desc(), RecoveryAction.id.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def unknown_actions(self, *, now: datetime, limit: int) -> list[RecoveryAction]:
        statement = (
            select(RecoveryAction)
            .where(
                RecoveryAction.status == ActionStatus.UNKNOWN.value,
                RecoveryAction.unknown_since <= now,
                RecoveryAction.dead_lettered_at.is_(None),
            )
            .order_by(RecoveryAction.unknown_since)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def actions_for_reconciliation(
        self, *, now: datetime, limit: int
    ) -> list[RecoveryAction]:
        statement = (
            select(RecoveryAction)
            .join(
                RecoveryCase,
                (RecoveryCase.merchant_id == RecoveryAction.merchant_id)
                & (RecoveryCase.id == RecoveryAction.recovery_case_id),
            )
            .where(
                RecoveryAction.status.in_(
                    (ActionStatus.UNKNOWN.value, ActionStatus.SUCCEEDED.value)
                ),
                RecoveryAction.dead_lettered_at.is_(None),
                RecoveryCase.state.in_(("UNKNOWN", "VERIFYING")),
                or_(RecoveryAction.unknown_since.is_(None), RecoveryAction.unknown_since <= now),
            )
            .order_by(RecoveryAction.updated_at, RecoveryAction.merchant_id, RecoveryAction.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def cancel_before_execution(
        self,
        *,
        action: RecoveryAction,
        cancelled_at: datetime,
        reason_code: str,
    ) -> None:
        if action.status != ActionStatus.PENDING.value:
            raise ActionPersistenceError(
                "ACTION_NOT_PENDING",
                "only a pending action can be cancelled before a provider attempt",
            )
        action.status = ActionStatus.FAILED.value
        action.last_error_code = reason_code
        action.dead_lettered_at = cancelled_at
        action.lease_token = None
        action.lease_expires_at = None
        action.updated_at = cancelled_at
        await self._session.flush()

    async def set_action_resolution(
        self,
        *,
        action: RecoveryAction,
        status: ActionStatus,
        resolved_at: datetime,
        provider_object_id: str | None,
        error_code: str | None = None,
    ) -> None:
        action.status = status.value
        action.provider_object_id = provider_object_id or action.provider_object_id
        action.last_error_code = error_code
        action.unknown_since = None
        action.lease_token = None
        action.lease_expires_at = None
        action.updated_at = resolved_at
        if status is ActionStatus.FAILED:
            action.dead_lettered_at = resolved_at
        await self._session.flush()

    async def recovered_totals(self, *, merchant_id: str) -> tuple[RecoveryTotals, ...]:
        rows = (
            await self._session.execute(
                select(
                    VerifiedOutcome.currency,
                    func.coalesce(func.sum(VerifiedOutcome.recovered_amount_minor), 0),
                    func.count(VerifiedOutcome.id),
                )
                .where(
                    VerifiedOutcome.merchant_id == merchant_id,
                    VerifiedOutcome.outcome_status == ActionStatus.SUCCEEDED.value,
                    VerifiedOutcome.is_authoritative.is_(True),
                    VerifiedOutcome.recovered_amount_minor > 0,
                )
                .group_by(VerifiedOutcome.currency)
                .order_by(VerifiedOutcome.currency)
            )
        ).all()
        return tuple(
            RecoveryTotals(merchant_id, str(currency), int(amount), int(count))
            for currency, amount, count in rows
        )


def _action_from_row(row: RecoveryAction) -> DomainRecoveryAction:
    parameters: dict[str, object] = dict(row.parameters)
    if row.provider_object_id is not None:
        parameters["provider_object_id"] = row.provider_object_id
    return DomainRecoveryAction(
        action_id=row.id,
        case_id=row.recovery_case_id,
        merchant_id=row.merchant_id,
        decision_receipt_id=row.decision_receipt_id,
        action_type=ActionType(row.action_type),
        target_type=SubjectType(row.target_type),
        target_id=row.target_id,
        logical_attempt=row.logical_attempt,
        idempotency_key=row.idempotency_key,
        status=ActionStatus(row.status),
        parameters=parameters,
        authorized_at=row.authorized_at,
        execute_after=row.execute_after,
        created_at=row.created_at,
        updated_at=row.updated_at,
        schema_version=row.schema_version,
    )
