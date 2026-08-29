"""Scheduled maintenance for durable Phase 6 receivables workflows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TypedDict

from revenueguard_integrations.persistence import (
    PlaybookRepository,
    RecoveryRepository,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from revenueguard_integrations.playbooks import ReceivablesPlaybookService

from revenueguard_worker.celery_app import celery_app
from revenueguard_worker.config import get_worker_settings

PROMISE_MAINTENANCE_BATCH_SIZE = 100

settings = get_worker_settings()
engine = create_database_engine(settings.database_url, use_null_pool=True)
session_factory = create_session_factory(engine)


class PromiseMaintenanceTaskResult(TypedDict):
    reminders_considered: int
    reminders_authorized: int
    broken_promises_escalated: int


@celery_app.task(name="revenueguard.playbooks.maintain_promises")  # type: ignore[untyped-decorator]
def maintain_promises() -> PromiseMaintenanceTaskResult:
    return asyncio.run(_maintain_promises())


async def _maintain_promises() -> PromiseMaintenanceTaskResult:
    now = datetime.now(UTC)
    async with session_scope(session_factory) as session:
        service = ReceivablesPlaybookService(
            PlaybookRepository(session),
            RecoveryRepository(session),
            clock=lambda: now,
        )
        broken = await service.escalate_broken_promises(
            due_at=now,
            limit=PROMISE_MAINTENANCE_BATCH_SIZE,
        )
        reminders = await service.schedule_due_promise_reminders(
            due_at=now,
            limit=PROMISE_MAINTENANCE_BATCH_SIZE,
        )
    return {
        "reminders_considered": len(reminders),
        "reminders_authorized": sum(
            item.disposition == "REMINDER_AUTHORIZED" for item in reminders
        ),
        "broken_promises_escalated": sum(
            item.disposition == "BROKEN_PROMISE_ESCALATED" for item in broken
        ),
    }
