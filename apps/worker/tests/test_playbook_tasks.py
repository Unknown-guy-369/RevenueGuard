from __future__ import annotations

from revenueguard_worker import playbook_tasks  # noqa: F401
from revenueguard_worker.celery_app import celery_app


def test_promise_maintenance_is_registered_on_a_dedicated_queue() -> None:
    assert "revenueguard.playbooks.maintain_promises" in celery_app.tasks
    assert celery_app.conf.task_routes["revenueguard.playbooks.maintain_promises"] == {
        "queue": "playbook_maintenance"
    }
    assert celery_app.conf.beat_schedule["maintain-durable-promises"] == {
        "task": "revenueguard.playbooks.maintain_promises",
        "schedule": 60.0,
    }


def test_portfolio_maintenance_is_registered_on_a_dedicated_queue() -> None:
    assert "revenueguard.portfolio.maintain" in celery_app.tasks
    assert celery_app.conf.task_routes["revenueguard.portfolio.maintain"] == {
        "queue": "portfolio_maintenance"
    }
    assert celery_app.conf.beat_schedule["maintain-portfolio-intelligence"] == {
        "task": "revenueguard.portfolio.maintain",
        "schedule": 60.0,
    }
