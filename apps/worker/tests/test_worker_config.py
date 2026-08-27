import pytest
from pydantic import ValidationError
from revenueguard_agents import AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS
from revenueguard_worker.celery_app import celery_app
from revenueguard_worker.config import AgentModelProvider, WorkerSettings
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


def test_agent_budgets_are_bounded_and_workflow_timeout_covers_model_timeout() -> None:
    settings = WorkerSettings(_env_file=None)

    assert settings.agent_model_max_retries <= 3
    assert settings.agent_model_max_output_tokens <= 4_096
    assert settings.agent_graph_max_steps <= 32
    assert settings.agent_workflow_timeout_seconds >= settings.agent_model_timeout_seconds

    with pytest.raises(ValidationError, match="workflow timeout"):
        WorkerSettings(
            _env_file=None,
            agent_model_timeout_seconds=5,
            agent_workflow_timeout_seconds=1,
        )


def test_openai_compatible_model_configuration_is_explicit_and_local_friendly() -> None:
    settings = WorkerSettings(
        _env_file=None,
        agent_model_provider="OPENAI_COMPATIBLE",
        agent_model_base_url="http://localhost:11434/v1",
        agent_model_name="local-model",
        agent_model_response_mode="JSON_OBJECT",
        agent_model_token_limit_field="MAX_TOKENS",
    )

    assert settings.agent_model_provider is AgentModelProvider.OPENAI_COMPATIBLE
    assert settings.llm_api_key is None

    with pytest.raises(ValidationError, match="base URL"):
        WorkerSettings(
            _env_file=None,
            agent_model_provider="OPENAI_COMPATIBLE",
            agent_model_name="missing-url-model",
        )
