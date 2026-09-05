from __future__ import annotations

from pathlib import Path

from revenueguard_worker import tasks  # noqa: F401
from revenueguard_worker.celery_app import celery_app

ROOT = Path(__file__).resolve().parents[3]


def test_due_deferred_cases_are_registered_and_scheduled_on_a_dedicated_queue() -> None:
    assert "revenueguard.cases.reevaluate_deferred" in celery_app.tasks
    assert celery_app.conf.task_routes["revenueguard.cases.reevaluate_deferred"] == {
        "queue": "case_reevaluation"
    }
    assert celery_app.conf.beat_schedule["reevaluate-due-deferred-cases"] == {
        "task": "revenueguard.cases.reevaluate_deferred",
        "schedule": 30.0,
    }


def test_worker_launch_paths_consume_the_deferred_case_queue() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    dockerfile = (ROOT / "deploy/docker/worker.Dockerfile").read_text(encoding="utf-8")

    assert (
        "--queues=celery,event_dispatch,event_ingestion,action_dispatch,"
        "action_execution,action_reconciliation,case_reevaluation,"
        "playbook_maintenance" in makefile
    )
    assert (
        "--queues=celery,event_dispatch,event_ingestion,action_dispatch,"
        "action_execution,action_reconciliation,case_reevaluation,"
        "playbook_maintenance" in dockerfile
    )
