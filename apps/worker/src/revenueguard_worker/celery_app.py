"""Celery application with loss-aware worker defaults."""

from celery import Celery

from revenueguard_worker.config import get_worker_settings

settings = get_worker_settings()

celery_app = Celery(
    "revenueguard",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["revenueguard_worker.tasks", "revenueguard_worker.playbook_tasks"],
)

celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    timezone="UTC",
    worker_prefetch_multiplier=1,
    beat_schedule={
        "dispatch-durable-webhook-events": {
            "task": "revenueguard.events.dispatch_pending",
            "schedule": 5.0,
        },
        "dispatch-durable-recovery-actions": {
            "task": "revenueguard.actions.dispatch_pending",
            "schedule": 5.0,
        },
        "reconcile-unknown-recovery-actions": {
            "task": "revenueguard.actions.reconcile_unknown",
            "schedule": 30.0,
        },
        "reevaluate-due-deferred-cases": {
            "task": "revenueguard.cases.reevaluate_deferred",
            "schedule": 30.0,
        },
        "maintain-durable-promises": {
            "task": "revenueguard.playbooks.maintain_promises",
            "schedule": 60.0,
        },
        "maintain-portfolio-intelligence": {
            "task": "revenueguard.portfolio.maintain",
            "schedule": 60.0,
        },
    },
    task_routes={
        "revenueguard.events.dispatch_pending": {"queue": "event_dispatch"},
        "revenueguard.events.process": {"queue": "event_ingestion"},
        "revenueguard.actions.dispatch_pending": {"queue": "action_dispatch"},
        "revenueguard.actions.execute": {"queue": "action_execution"},
        "revenueguard.actions.reconcile_unknown": {"queue": "action_reconciliation"},
        "revenueguard.cases.reevaluate_deferred": {"queue": "case_reevaluation"},
        "revenueguard.playbooks.maintain_promises": {"queue": "playbook_maintenance"},
        "revenueguard.portfolio.maintain": {"queue": "portfolio_maintenance"},
    },
)
