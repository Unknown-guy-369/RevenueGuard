"""Durable event ingestion, action execution, and outcome reconciliation tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict, cast

from revenueguard_agents import (
    AgentBudget,
    BoundedCaseIntelligence,
    LangSmithCaseIntelligenceTracer,
    LangSmithTracingConfig,
    OpenAICompatibleStructuredModel,
)
from revenueguard_domain import ActionType, EventSource, RecoveryAction
from revenueguard_integrations.execution import (
    ActionExecutionService,
    ActionProvider,
    DeterministicSimulatorAdapter,
    ExecutionDisposition,
    RazorpayTestModeAdapter,
)
from revenueguard_integrations.persistence import (
    ActionRepository,
    EventDispatch,
    EventIngestionRepository,
    RecoveryRepository,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from revenueguard_integrations.razorpay import (
    RazorpayEventError,
    normalize_razorpay_event,
)
from revenueguard_integrations.recovery import RecoveryApplicationService
from sqlalchemy import select

from revenueguard_worker.celery_app import celery_app
from revenueguard_worker.config import AgentModelProvider, WorkerSettings, get_worker_settings


def _build_case_intelligence(worker_settings: WorkerSettings) -> BoundedCaseIntelligence:
    model = None
    if worker_settings.agent_model_provider is AgentModelProvider.OPENAI_COMPATIBLE:
        if worker_settings.agent_model_base_url is None or worker_settings.agent_model_name is None:
            raise AssertionError("validated OpenAI-compatible model settings are incomplete")
        api_key = (
            worker_settings.llm_api_key.get_secret_value()
            if worker_settings.llm_api_key is not None
            else None
        )
        model = OpenAICompatibleStructuredModel(
            base_url=worker_settings.agent_model_base_url,
            model_name=worker_settings.agent_model_name,
            api_key=api_key,
            response_mode=worker_settings.agent_model_response_mode,
            token_limit_field=worker_settings.agent_model_token_limit_field,
            timeout_seconds=worker_settings.agent_model_timeout_seconds,
        )
    langsmith_api_key = (
        worker_settings.langsmith_api_key.get_secret_value()
        if worker_settings.langsmith_api_key is not None
        else None
    )
    return BoundedCaseIntelligence(
        model,
        budget=AgentBudget(
            model_timeout_seconds=worker_settings.agent_model_timeout_seconds,
            workflow_timeout_seconds=worker_settings.agent_workflow_timeout_seconds,
            max_model_retries=worker_settings.agent_model_max_retries,
            max_output_tokens=worker_settings.agent_model_max_output_tokens,
            max_graph_steps=worker_settings.agent_graph_max_steps,
        ),
        tracer=LangSmithCaseIntelligenceTracer(
            LangSmithTracingConfig(
                enabled=worker_settings.langsmith_tracing_enabled,
                project_name=worker_settings.langsmith_project,
                api_key=langsmith_api_key,
            )
        ),
    )


settings = get_worker_settings()
engine = create_database_engine(settings.database_url, use_null_pool=True)
session_factory = create_session_factory(engine)
case_intelligence = _build_case_intelligence(settings)


class PingResult(TypedDict):
    status: Literal["ok"]
    service: str


class DispatchResult(TypedDict):
    claimed: int
    published: int
    rescheduled: int


class ProcessingResult(TypedDict):
    dispatch_id: str
    status: Literal["processed", "already_processed", "dead_letter", "retry_scheduled"]


class ActionDispatchResult(TypedDict):
    claimed: int
    published: int
    rescheduled: int


class ActionExecutionResult(TypedDict):
    action_id: str
    status: str
    case_state: str


class ReconciliationResult(TypedDict):
    stale_calls_marked_unknown: int
    reconciled: int


@celery_app.task(name="revenueguard.system.ping")  # type: ignore[untyped-decorator]
def ping() -> PingResult:
    """Prove worker registration without touching financial state."""

    return {"status": "ok", "service": "revenueguard-worker"}


@celery_app.task(name="revenueguard.events.dispatch_pending")  # type: ignore[untyped-decorator]
def dispatch_pending_events() -> DispatchResult:
    """Publish due PostgreSQL dispatch rows and retain broker failures durably."""

    return asyncio.run(_dispatch_pending_events())


@celery_app.task(name="revenueguard.events.process")  # type: ignore[untyped-decorator]
def process_webhook_event(
    dispatch_id: str,
    merchant_id: str,
    webhook_event_id: str,
) -> ProcessingResult:
    """Normalize one accepted event idempotently; never perform a recovery action."""

    try:
        return asyncio.run(_process_webhook_event(dispatch_id, merchant_id, webhook_event_id))
    except RazorpayEventError as error:
        asyncio.run(
            _record_processing_failure(
                dispatch_id,
                error_code=error.code,
                error_detail=str(error)[:500],
                terminal=True,
            )
        )
        return {"dispatch_id": dispatch_id, "status": "dead_letter"}
    except Exception:
        state = asyncio.run(
            _record_processing_failure(
                dispatch_id,
                error_code="TRANSIENT_PROCESSING_ERROR",
                error_detail="transient worker processing failure",
                terminal=False,
            )
        )
        status: Literal["dead_letter", "retry_scheduled"] = (
            "dead_letter" if state == "DEAD_LETTER" else "retry_scheduled"
        )
        return {"dispatch_id": dispatch_id, "status": status}


@celery_app.task(name="revenueguard.actions.dispatch_pending")  # type: ignore[untyped-decorator]
def dispatch_pending_actions() -> ActionDispatchResult:
    return asyncio.run(_dispatch_pending_actions())


@celery_app.task(name="revenueguard.actions.execute")  # type: ignore[untyped-decorator]
def execute_recovery_action(
    merchant_id: str,
    action_id: str,
    lease_token: str,
) -> ActionExecutionResult:
    return asyncio.run(_execute_recovery_action(merchant_id, action_id, lease_token))


@celery_app.task(name="revenueguard.actions.reconcile_unknown")  # type: ignore[untyped-decorator]
def reconcile_unknown_actions() -> ReconciliationResult:
    return asyncio.run(_reconcile_unknown_actions())


async def _dispatch_pending_events() -> DispatchResult:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        claimed = await EventIngestionRepository(session).claim_dispatches(
            now=now,
            lease_for=timedelta(seconds=settings.event_dispatch_stale_after_seconds),
            limit=settings.event_dispatch_batch_size,
        )

    published = 0
    rescheduled = 0
    for dispatch in claimed:
        if dispatch.lease_token is None:
            continue
        task_id = f"event-dispatch-{dispatch.id}-attempt-{dispatch.attempt_count}"
        try:
            process_webhook_event.apply_async(
                args=[dispatch.id, dispatch.merchant_id, dispatch.webhook_event_id],
                task_id=task_id,
                queue=dispatch.queue_name,
            )
        except Exception:
            await _record_processing_failure(
                dispatch.id,
                error_code="BROKER_PUBLISH_FAILED",
                error_detail="broker publication failed",
                terminal=False,
            )
            rescheduled += 1
            continue

        async with session_scope(session_factory) as session:
            marked = await EventIngestionRepository(session).mark_dispatch_published(
                dispatch_id=dispatch.id,
                lease_token=dispatch.lease_token,
                broker_task_id=task_id,
                published_at=datetime.now(UTC),
            )
        if marked:
            published += 1

    return {"claimed": len(claimed), "published": published, "rescheduled": rescheduled}


async def _process_webhook_event(
    dispatch_id: str,
    merchant_id: str,
    webhook_event_id: str,
) -> ProcessingResult:
    async with session_scope(session_factory) as session:
        repository = EventIngestionRepository(session)
        dispatch = await session.scalar(
            select(EventDispatch).where(
                EventDispatch.id == dispatch_id,
                EventDispatch.merchant_id == merchant_id,
                EventDispatch.webhook_event_id == webhook_event_id,
            )
        )
        if dispatch is None:
            raise LookupError("tenant-scoped dispatch does not exist")
        if dispatch.state == "SUCCEEDED":
            return {"dispatch_id": dispatch_id, "status": "already_processed"}
        if dispatch.state == "DEAD_LETTER":
            return {"dispatch_id": dispatch_id, "status": "dead_letter"}

        webhook = await repository.fetch_webhook_for_processing(
            merchant_id=merchant_id,
            webhook_event_id=webhook_event_id,
            for_update=True,
        )
        if webhook is None or webhook.raw_body is None:
            raise LookupError("verified webhook body is unavailable")

        event = normalize_razorpay_event(
            webhook.raw_body,
            merchant_id=merchant_id,
            provider_event_id=cast(str, webhook.provider_event_id),
            event_id=f"evt_{webhook.id}",
            received_at=webhook.received_at,
            correlation_id=webhook.correlation_id,
            source_payload_reference=f"webhook_events/{webhook.provider_event_id}",
            source=(
                EventSource.SYNTHETIC if webhook.provider == "SIMULATOR" else EventSource.RAZORPAY
            ),
        )
        document = cast(Mapping[str, object], webhook.raw_payload or {})
        await _upsert_provider_entities(repository, event.to_dict(), document)
        normalized = await repository.persist_normalized_event(
            event=event.to_dict(),
            webhook_event_id=webhook_event_id,
            correlations=_event_correlations(event.to_dict()),
        )
        action_repository = ActionRepository(session)
        await RecoveryApplicationService(
            RecoveryRepository(session),
            action_repository=action_repository,
            case_intelligence=case_intelligence,
        ).process_event(
            merchant_id=merchant_id,
            normalized_event_id=normalized.id,
        )
        await ActionExecutionService(
            action_repository,
            RecoveryRepository(session),
            unknown_ttl=timedelta(seconds=settings.action_unknown_ttl_seconds),
        ).verify_signed_event(event=event, webhook_event_id=webhook_event_id)
        await repository.mark_dispatch_succeeded(
            dispatch_id=dispatch_id,
            completed_at=datetime.now(UTC),
        )
    return {"dispatch_id": dispatch_id, "status": "processed"}


async def _dispatch_pending_actions() -> ActionDispatchResult:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        claimed = await ActionRepository(session).claim_due_actions(
            now=now,
            lease_for=timedelta(seconds=settings.action_dispatch_stale_after_seconds),
            limit=settings.action_dispatch_batch_size,
        )
    published = 0
    rescheduled = 0
    for claim in claimed:
        task_id = f"action-{claim.action_id}-{claim.lease_token}"
        try:
            execute_recovery_action.apply_async(
                args=[claim.merchant_id, claim.action_id, claim.lease_token],
                task_id=task_id,
                queue="action_execution",
            )
            published += 1
        except Exception:
            # The lease expires and makes the never-started action claimable again.
            rescheduled += 1
    return {"claimed": len(claimed), "published": published, "rescheduled": rescheduled}


async def _execute_recovery_action(
    merchant_id: str,
    action_id: str,
    lease_token: str,
) -> ActionExecutionResult:
    async with session_scope(session_factory) as session:
        service = ActionExecutionService(
            ActionRepository(session),
            RecoveryRepository(session),
            unknown_ttl=timedelta(seconds=settings.action_unknown_ttl_seconds),
        )
        prepared = await service.prepare_execution(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            started_at=datetime.now(UTC),
        )

    if isinstance(prepared, ExecutionDisposition):
        return {
            "action_id": action_id,
            "status": prepared.action_status.value,
            "case_state": prepared.case_state.value,
        }

    provider = _provider_for(prepared.action)
    provider_result = await provider.execute(prepared.action)

    async with session_scope(session_factory) as session:
        disposition = await ActionExecutionService(
            ActionRepository(session),
            RecoveryRepository(session),
            unknown_ttl=timedelta(seconds=settings.action_unknown_ttl_seconds),
        ).record_execution_result(
            merchant_id=merchant_id,
            action_id=action_id,
            lease_token=lease_token,
            result=provider_result,
        )
    return {
        "action_id": action_id,
        "status": disposition.action_status.value,
        "case_state": disposition.case_state.value,
    }


async def _reconcile_unknown_actions() -> ReconciliationResult:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        service = ActionExecutionService(
            ActionRepository(session),
            RecoveryRepository(session),
            unknown_ttl=timedelta(seconds=settings.action_unknown_ttl_seconds),
        )
        stale = await service.mark_stale_calls_unknown(
            now=now,
            limit=settings.action_reconciliation_batch_size,
        )

    async with session_scope(session_factory) as session:
        rows = await ActionRepository(session).actions_for_reconciliation(
            now=now,
            limit=settings.action_reconciliation_batch_size,
        )
        identities = tuple((row.merchant_id, row.id) for row in rows)

    reconciled = 0
    for merchant_id, action_id in identities:
        async with session_scope(session_factory) as session:
            action = await ActionRepository(session).domain_action(
                merchant_id=merchant_id,
                action_id=action_id,
            )
        if action is None:
            continue
        lookup = await _provider_for(action).lookup(action)
        async with session_scope(session_factory) as session:
            await ActionExecutionService(
                ActionRepository(session),
                RecoveryRepository(session),
                unknown_ttl=timedelta(seconds=settings.action_unknown_ttl_seconds),
            ).record_lookup(
                merchant_id=merchant_id,
                action_id=action_id,
                result=lookup,
            )
        reconciled += 1
    return {"stale_calls_marked_unknown": len(stale), "reconciled": reconciled}


def _provider_for(action: RecoveryAction) -> ActionProvider:
    if (
        settings.action_provider == "RAZORPAY_TEST"
        and action.action_type is ActionType.CREATE_PAYMENT_LINK
    ):
        if settings.razorpay_key_id is None or settings.razorpay_key_secret is None:
            raise RuntimeError("Razorpay Test Mode execution requires configured credentials")
        return RazorpayTestModeAdapter(
            key_id=settings.razorpay_key_id.get_secret_value(),
            key_secret=settings.razorpay_key_secret.get_secret_value(),
            timeout_seconds=settings.razorpay_timeout_seconds,
        )
    return DeterministicSimulatorAdapter()


async def _record_processing_failure(
    dispatch_id: str,
    *,
    error_code: str,
    error_detail: str,
    terminal: bool,
) -> str:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        repository = EventIngestionRepository(session)
        dispatch = await session.get(EventDispatch, dispatch_id, with_for_update=True)
        if dispatch is None:
            raise LookupError("dispatch does not exist")
        retry_delay_seconds = min(300, 5 * (2 ** max(dispatch.attempt_count - 1, 0)))
        if terminal:
            dispatch.attempt_count = dispatch.max_attempts
        result = await repository.record_dispatch_failure(
            dispatch_id=dispatch_id,
            now=now,
            retry_at=now + timedelta(seconds=retry_delay_seconds),
            error_code=error_code,
            error_detail=error_detail,
        )
    return result.state


async def _upsert_provider_entities(
    repository: EventIngestionRepository,
    event: Mapping[str, object],
    document: Mapping[str, object],
) -> None:
    merchant_id = cast(str, event["merchant_id"])
    occurred_at = _event_datetime(event["occurred_at"])
    customer_id = cast(str | None, event.get("customer_id"))
    if customer_id is not None:
        await repository.upsert_customer(
            merchant_id=merchant_id,
            customer_id=customer_id,
            provider_customer_id=customer_id,
            provider_updated_at=occurred_at,
        )

    payment_id = cast(str | None, event.get("payment_id"))
    payment = _provider_entity(document, "payment")
    if payment_id is not None:
        payment_occurred = _provider_timestamp(payment, "created_at") or occurred_at
        await repository.upsert_payment(
            merchant_id=merchant_id,
            payment_id=payment_id,
            provider_payment_id=payment_id,
            customer_id=customer_id,
            order_id=cast(str | None, event.get("order_id")),
            amount_minor=cast(int, event["amount_minor"]),
            currency=cast(str, event["currency"]),
            status=_entity_status(payment, cast(str, event["event_type"])),
            provider_occurred_at=payment_occurred,
            provider_updated_at=occurred_at,
        )

    subscription_id = cast(str | None, event.get("subscription_id"))
    subscription = _provider_entity(document, "subscription")
    if subscription_id is not None:
        subscription_occurred = _provider_timestamp(subscription, "created_at") or occurred_at
        await repository.upsert_subscription(
            merchant_id=merchant_id,
            subscription_id=subscription_id,
            provider_subscription_id=subscription_id,
            customer_id=customer_id,
            amount_minor=cast(int, event["amount_minor"]),
            currency=cast(str, event["currency"]),
            status=_entity_status(subscription, cast(str, event["event_type"])),
            provider_occurred_at=subscription_occurred,
            provider_updated_at=occurred_at,
        )


def _event_correlations(event: Mapping[str, object]) -> tuple[dict[str, str | None], ...]:
    field_types = (
        ("customer_id", "CUSTOMER"),
        ("payment_id", "PAYMENT"),
        ("order_id", "ORDER"),
        ("subscription_id", "SUBSCRIPTION"),
        ("invoice_id", "INVOICE"),
        ("payment_link_id", "PAYMENT_LINK"),
    )
    correlations: list[dict[str, str | None]] = []
    for field, reference_type in field_types:
        identifier = event.get(field)
        if isinstance(identifier, str):
            internal_id = (
                identifier if field in {"customer_id", "payment_id", "subscription_id"} else None
            )
            correlations.append(
                {
                    "reference_type": reference_type,
                    "external_id": identifier,
                    "internal_id": internal_id,
                }
            )
    return tuple(correlations)


def _provider_entity(
    document: Mapping[str, object], entity_type: str
) -> Mapping[str, object] | None:
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return None
    wrapper = payload.get(entity_type)
    if not isinstance(wrapper, dict):
        return None
    entity = wrapper.get("entity")
    return cast(Mapping[str, object], entity) if isinstance(entity, dict) else None


def _entity_status(entity: Mapping[str, object] | None, event_type: str) -> str:
    if entity is not None and isinstance(entity.get("status"), str):
        return cast(str, entity["status"]).upper()
    return event_type.rsplit(".", maxsplit=1)[-1].upper()


def _provider_timestamp(entity: Mapping[str, object] | None, key: str) -> datetime | None:
    if entity is None:
        return None
    timestamp = entity.get(key)
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except OverflowError, OSError, ValueError:
        return None


def _event_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("normalized event timestamp must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("normalized event timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
