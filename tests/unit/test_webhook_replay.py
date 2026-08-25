from __future__ import annotations

import json
from pathlib import Path

import pytest
import revenueguard_evaluation.webhook_replay as replay_module
from revenueguard_evaluation.webhook_replay import (
    ReplayDelivery,
    ReplayMode,
    ReplayResponse,
    load_fixtures,
    plan_replay,
    run_replay,
)


def delivery(name: str, event_id: str) -> ReplayDelivery:
    return ReplayDelivery(Path(name), b'{"event":"payment.failed"}', event_id)


def test_load_fixtures_derives_stable_event_ids(tmp_path: Path) -> None:
    fixture = tmp_path / "payment_failed.json"
    fixture.write_text('{"event":"payment.failed"}', encoding="utf-8")

    first = load_fixtures([fixture])[0]
    second = load_fixtures([fixture])[0]

    assert first.provider_event_id == second.provider_event_id
    assert first.raw_body == fixture.read_bytes()


@pytest.mark.parametrize(
    ("mode", "expected_ids"),
    [
        (ReplayMode.NORMAL, ["first", "second"]),
        (ReplayMode.DELAYED, ["first", "second"]),
        (ReplayMode.OUT_OF_ORDER, ["second", "first"]),
    ],
)
def test_replay_plans_preserve_expected_order(mode: ReplayMode, expected_ids: list[str]) -> None:
    fixtures = [delivery("first.json", "first"), delivery("second.json", "second")]

    assert [item.provider_event_id for item in plan_replay(fixtures, mode)] == expected_ids


def test_duplicate_plan_uses_one_stable_provider_event_id_five_times() -> None:
    plan = plan_replay([delivery("one.json", "one")], ReplayMode.DUPLICATE)

    assert len(plan) == 5
    assert {item.provider_event_id for item in plan} == {"one"}


def test_invalid_signature_plan_is_explicit() -> None:
    plan = plan_replay([delivery("one.json", "one")], ReplayMode.INVALID_SIGNATURE)

    assert len(plan) == 1
    assert plan[0].valid_signature is False


def test_burst_repeats_fixtures_to_requested_size() -> None:
    fixtures = [delivery("first.json", "first"), delivery("second.json", "second")]

    plan = plan_replay(fixtures, ReplayMode.BURST, burst_size=5)

    assert [item.provider_event_id for item in plan] == [
        "first",
        "second",
        "first",
        "second",
        "first",
    ]


def test_delayed_replay_uses_injected_clock_and_summarizes_outcomes() -> None:
    waits: list[float] = []
    fixtures = [delivery("first.json", "first"), delivery("second.json", "second")]

    def sender(item: ReplayDelivery) -> ReplayResponse:
        outcome = "duplicate" if item.provider_event_id == "second" else "accepted"
        status = 200 if outcome == "duplicate" else 202
        return ReplayResponse(item.provider_event_id, status, outcome)

    summary = run_replay(
        fixtures,
        ReplayMode.DELAYED,
        sender,
        delay_seconds=0.25,
        sleeper=waits.append,
    )

    assert waits == [0.25]
    assert summary.as_dict() == {
        "mode": "delayed",
        "received": 2,
        "accepted": 1,
        "duplicates": 1,
        "rejected": 0,
        "failures": 0,
    }


def test_non_object_fixture_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "unsafe.json"
    fixture.write_text(json.dumps(["not", "an", "event"]), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_fixtures([fixture])


def test_http_sender_reads_api_status_as_replay_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, _: int) -> bytes:
            return b'{"status":"duplicate","provider_event_id":"event_001"}'

    monkeypatch.setattr(replay_module, "urlopen", lambda *_args, **_kwargs: Response())
    sender = replay_module.make_http_sender(
        "http://localhost/webhook",
        "merchant_001",
        "test-secret-not-a-live-credential",
    )

    result = sender(delivery("payment_failed.json", "event_001"))

    assert result.status_code == 200
    assert result.outcome == "duplicate"
