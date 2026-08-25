from pathlib import Path

from revenueguard_agents import AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS

ROOT = Path(__file__).resolve().parents[1]


def test_phase1_expected_entrypoints_exist() -> None:
    expected = [
        "apps/api/src/revenueguard_api/main.py",
        "apps/worker/src/revenueguard_worker/celery_app.py",
        "apps/web/app/page.tsx",
        "migrations/versions/0001_phase1_baseline.py",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
    ]

    missing = [path for path in expected if not (ROOT / path).is_file()]
    assert missing == []


def test_example_environment_contains_no_external_secret_values() -> None:
    values: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    for secret_name in (
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
        "LLM_API_KEY",
    ):
        assert values[secret_name] == ""


def test_container_images_use_versioned_tags() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: postgres:17-alpine" in compose
    assert "image: redis:7.4-alpine" in compose
    assert ":latest" not in compose


def test_agent_execution_boundary_remains_closed() -> None:
    assert AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS is False
