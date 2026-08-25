"""Deterministic Razorpay webhook replay planning and delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ReplayMode(StrEnum):
    """Supported delivery failure and ordering scenarios."""

    NORMAL = "normal"
    DUPLICATE = "duplicate"
    INVALID_SIGNATURE = "invalid-signature"
    DELAYED = "delayed"
    BURST = "burst"
    OUT_OF_ORDER = "out-of-order"


@dataclass(frozen=True, slots=True)
class ReplayDelivery:
    """One planned HTTP delivery."""

    fixture: Path
    raw_body: bytes
    provider_event_id: str
    valid_signature: bool = True


@dataclass(frozen=True, slots=True)
class ReplayResponse:
    """Bounded response information retained by the harness."""

    provider_event_id: str
    status_code: int
    outcome: str | None


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """Machine-readable aggregate for a replay run."""

    mode: ReplayMode
    received: int
    accepted: int
    duplicates: int
    rejected: int
    failures: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "mode": self.mode.value,
            "received": self.received,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "failures": self.failures,
        }


Sender = Callable[[ReplayDelivery], ReplayResponse]
Sleeper = Callable[[float], None]


def _fixture_event_id(path: Path, raw_body: bytes) -> str:
    digest = hashlib.sha256(path.name.encode() + b"\0" + raw_body).hexdigest()[:24]
    return f"replay_{digest}"


def load_fixtures(paths: Sequence[Path]) -> list[ReplayDelivery]:
    """Load bounded JSON fixtures and derive stable provider event IDs."""

    deliveries: list[ReplayDelivery] = []
    for path in paths:
        raw_body = path.read_bytes()
        parsed = json.loads(raw_body)
        if not isinstance(parsed, dict):
            raise ValueError(f"fixture must contain a JSON object: {path}")
        deliveries.append(
            ReplayDelivery(
                fixture=path,
                raw_body=raw_body,
                provider_event_id=_fixture_event_id(path, raw_body),
            )
        )
    if not deliveries:
        raise ValueError("at least one fixture is required")
    return deliveries


def plan_replay(
    fixtures: Sequence[ReplayDelivery],
    mode: ReplayMode,
    *,
    duplicate_count: int = 5,
    burst_size: int = 25,
) -> list[ReplayDelivery]:
    """Create a deterministic delivery plan without performing I/O."""

    if not fixtures:
        raise ValueError("at least one fixture is required")
    if duplicate_count < 2:
        raise ValueError("duplicate_count must be at least 2")
    if burst_size < 1:
        raise ValueError("burst_size must be positive")

    if mode in {ReplayMode.NORMAL, ReplayMode.DELAYED}:
        return list(fixtures)
    if mode is ReplayMode.DUPLICATE:
        return [fixtures[0]] * duplicate_count
    if mode is ReplayMode.INVALID_SIGNATURE:
        first = fixtures[0]
        return [
            ReplayDelivery(
                fixture=first.fixture,
                raw_body=first.raw_body,
                provider_event_id=first.provider_event_id,
                valid_signature=False,
            )
        ]
    if mode is ReplayMode.BURST:
        return [fixtures[index % len(fixtures)] for index in range(burst_size)]
    if len(fixtures) < 2:
        raise ValueError("out-of-order replay requires at least two fixtures")
    return list(reversed(fixtures))


def _summarize(mode: ReplayMode, responses: Sequence[ReplayResponse]) -> ReplaySummary:
    return ReplaySummary(
        mode=mode,
        received=len(responses),
        accepted=sum(response.status_code == 202 for response in responses),
        duplicates=sum(
            response.status_code == 200 and response.outcome == "duplicate"
            for response in responses
        ),
        rejected=sum(400 <= response.status_code < 500 for response in responses),
        failures=sum(
            response.status_code >= 500 or response.status_code == 0 for response in responses
        ),
    )


def run_replay(
    plan: Sequence[ReplayDelivery],
    mode: ReplayMode,
    sender: Sender,
    *,
    delay_seconds: float = 1.0,
    max_workers: int = 8,
    sleeper: Sleeper = time.sleep,
) -> ReplaySummary:
    """Deliver a plan and return bounded aggregate results."""

    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative")
    if max_workers < 1:
        raise ValueError("max_workers must be positive")

    if mode is ReplayMode.BURST:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            responses = list(executor.map(sender, plan))
    else:
        responses = []
        for index, delivery in enumerate(plan):
            if mode is ReplayMode.DELAYED and index > 0:
                sleeper(delay_seconds)
            responses.append(sender(delivery))
    return _summarize(mode, responses)


def make_http_sender(
    endpoint: str,
    merchant_id: str,
    webhook_secret: str,
    *,
    timeout_seconds: float = 10.0,
) -> Sender:
    """Create an HTTP sender that signs the exact fixture bytes."""

    secret_bytes = webhook_secret.encode()

    def send(delivery: ReplayDelivery) -> ReplayResponse:
        signature = hmac.new(secret_bytes, delivery.raw_body, hashlib.sha256).hexdigest()
        if not delivery.valid_signature:
            signature = "0" * 64
        request = Request(
            endpoint,
            data=delivery.raw_body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Event-Id": delivery.provider_event_id,
                "X-Razorpay-Signature": signature,
                "X-RevenueGuard-Merchant-Id": merchant_id,
            },
        )
        status_code = 0
        response_body = b""
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                status_code = response.status
                response_body = response.read(16_384)
        except HTTPError as error:
            status_code = error.code
            response_body = error.read(16_384)
        except URLError:
            return ReplayResponse(delivery.provider_event_id, 0, None)

        outcome: str | None = None
        try:
            parsed = json.loads(response_body)
            if isinstance(parsed, dict):
                response_outcome = parsed.get("outcome", parsed.get("status"))
                if isinstance(response_outcome, str):
                    outcome = response_outcome
        except UnicodeDecodeError, json.JSONDecodeError:
            outcome = None
        return ReplayResponse(delivery.provider_event_id, status_code, outcome)

    return send
