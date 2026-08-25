from revenueguard_agents import AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS
from revenueguard_worker.celery_app import celery_app
from revenueguard_worker.tasks import ping


def test_worker_uses_json_and_late_acknowledgement() -> None:
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_diagnostic_task_has_no_external_side_effect() -> None:
    assert ping.run() == {"status": "ok", "service": "revenueguard-worker"}


def test_agent_boundary_forbids_external_execution() -> None:
    assert AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS is False
