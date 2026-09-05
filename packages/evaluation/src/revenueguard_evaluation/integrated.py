"""Live local integration batch for the RevenueGuard synthetic demonstration path."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from revenueguard_evaluation.batch import HeldOutManifest


class IntegratedEvaluationError(RuntimeError):
    """The live system did not complete the bounded integration batch."""


class IntegratedApi(Protocol):
    def get(self, path: str) -> dict[str, Any]: ...

    def post(self, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]: ...


class HttpIntegratedApi:
    """Small authenticated JSON client for a running local RevenueGuard API."""

    def __init__(self, *, base_url: str, merchant_id: str, dashboard_token: str) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be HTTP or HTTPS")
        if not merchant_id or not dashboard_token:
            raise ValueError("merchant_id and dashboard_token are required")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {dashboard_token}",
            "Content-Type": "application/json",
            "X-RevenueGuard-Merchant-Id": merchant_id,
        }

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, None)

    def post(self, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
        return self._request("POST", path, payload or {})

    def _request(self, method: str, path: str, payload: dict[str, object] | None) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        request = Request(
            f"{self._base_url}{path}",
            data=body,
            headers=self._headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                document = json.loads(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1_000]
            raise IntegratedEvaluationError(
                f"{method} {path} returned HTTP {error.code}: {detail}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise IntegratedEvaluationError(f"{method} {path} failed: {error}") from error
        if not isinstance(document, dict):
            raise IntegratedEvaluationError(f"{method} {path} returned a non-object response")
        return cast(dict[str, Any], document)


@dataclass(frozen=True, slots=True)
class IntegratedScenario:
    name: str
    scenario: str
    amount_minor: int
    expected_state: str | None
    inject_recovery: bool = False


INTEGRATED_SCENARIOS = (
    IntegratedScenario("recover_auth_1", "AUTHENTICATION_FAILURE", 15_900, "RECOVERED", True),
    IntegratedScenario("recover_auth_2", "AUTHENTICATION_FAILURE", 24_900, "RECOVERED", True),
    IntegratedScenario("recover_auth_3", "AUTHENTICATION_FAILURE", 34_900, "RECOVERED", True),
    IntegratedScenario("high_value_review", "AUTHENTICATION_FAILURE", 75_000, "ESCALATED"),
    IntegratedScenario("insufficient_funds_defer", "INSUFFICIENT_FUNDS", 12_000, "DEFERRED"),
    IntegratedScenario("issuer_outage_defer", "ISSUER_OUTAGE", 18_000, "DEFERRED"),
    IntegratedScenario("timeout_safe_defer", "TIMEOUT", 21_000, "DEFERRED"),
    IntegratedScenario("already_successful", "SUCCESS", 9_900, None),
)


def run_integrated_batch(
    api: IntegratedApi,
    *,
    manifest: HeldOutManifest,
    timeout_seconds: float = 300,
    poll_interval_seconds: float = 1,
) -> dict[str, object]:
    """Exercise a running API/worker/database stack and report only observed records."""

    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("timeouts and polling interval must be positive")
    started_at = datetime.now(UTC)
    readiness = api.get("/ready")
    sessions: list[tuple[IntegratedScenario, str, str]] = []
    snapshots: dict[str, dict[str, Any]] = {}
    duplicate_event_id = ""
    deadline = time.monotonic() + timeout_seconds
    for index, scenario in enumerate(INTEGRATED_SCENARIOS):
        created = api.post(
            "/api/v1/simulations",
            {
                "scenario": scenario.scenario,
                "flow_type": "ONE_TIME",
                "amount_minor": scenario.amount_minor,
                "currency": "INR",
            },
        )
        simulation_id = _required_string(created, "simulation_id")
        submitted = api.post(f"/api/v1/public/simulations/{simulation_id}/attempt")
        provider_event_id = _required_string(submitted, "provider_event_id")
        sessions.append((scenario, simulation_id, provider_event_id))
        if index == 0:
            duplicate = api.post(f"/api/v1/public/simulations/{simulation_id}/attempt")
            duplicate_event_id = _required_string(duplicate, "provider_event_id")

        recovery_submitted = False
        while time.monotonic() < deadline:
            snapshot = api.get(f"/api/v1/simulations/{simulation_id}/events")
            snapshots[simulation_id] = snapshot
            if (
                scenario.inject_recovery
                and not recovery_submitted
                and _ready_for_recovery(snapshot)
            ):
                api.post(f"/api/v1/simulations/{simulation_id}/recovery-success")
                recovery_submitted = True
            if _scenario_complete(scenario, snapshot):
                break
            time.sleep(poll_interval_seconds)
        else:
            states = {
                item.name: snapshots.get(item_id, {}).get("case_state")
                for item, item_id, _ in sessions
            }
            raise IntegratedEvaluationError(
                f"integrated batch timed out after {timeout_seconds:g}s; observed states={states}"
            )

    case_details: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots.values():
        case_id = snapshot.get("case_id")
        if isinstance(case_id, str):
            case_details[case_id] = api.get(f"/api/v1/dashboard/cases/{case_id}")

    completed_at = datetime.now(UTC)
    return _build_report(
        manifest=manifest,
        readiness=readiness,
        sessions=sessions,
        snapshots=snapshots,
        case_details=case_details,
        duplicate_event_id=duplicate_event_id,
        started_at=started_at,
        completed_at=completed_at,
    )


def _ready_for_recovery(snapshot: dict[str, Any]) -> bool:
    return (
        snapshot.get("case_state") in {"VERIFYING", "UNKNOWN"}
        and snapshot.get("action_type") == "CREATE_PAYMENT_LINK"
        and snapshot.get("action_status") in {"SUCCEEDED", "UNKNOWN"}
    )


def _scenario_complete(scenario: IntegratedScenario, snapshot: dict[str, Any]) -> bool:
    if scenario.expected_state is None:
        return snapshot.get("status") == "COMPLETED"
    return snapshot.get("case_state") == scenario.expected_state


def _build_report(
    *,
    manifest: HeldOutManifest,
    readiness: dict[str, Any],
    sessions: list[tuple[IntegratedScenario, str, str]],
    snapshots: dict[str, dict[str, Any]],
    case_details: dict[str, dict[str, Any]],
    duplicate_event_id: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, object]:
    cases: list[dict[str, object]] = []
    recovered_minor = 0
    at_risk_minor = 0
    violations = 0
    unverified_counted = 0
    attempts_beyond_limit = 0
    incomplete_audits = 0
    for scenario, simulation_id, provider_event_id in sessions:
        snapshot = snapshots[simulation_id]
        if scenario.scenario != "SUCCESS":
            at_risk_minor += scenario.amount_minor
        recovered = int(snapshot.get("recovered_amount_minor", 0))
        authoritative = snapshot.get("outcome_authoritative") is True
        if recovered and authoritative:
            recovered_minor += recovered
        elif recovered:
            unverified_counted += 1
        case_id = snapshot.get("case_id")
        detail = case_details.get(case_id, {}) if isinstance(case_id, str) else {}
        transitions = detail.get("transitions", [])
        decisions = detail.get("decisions", [])
        actions = detail.get("actions", [])
        outcomes = detail.get("outcomes", [])
        if not all(
            isinstance(items, list) for items in (transitions, decisions, actions, outcomes)
        ):
            raise IntegratedEvaluationError("case detail returned invalid audit collections")
        typed_actions = cast(list[dict[str, Any]], actions)
        for action in typed_actions:
            if int(action.get("attempt_count", 0)) > int(action.get("max_attempts", 0)):
                attempts_beyond_limit += 1
        if typed_actions and snapshot.get("policy_result") != "PROCEED":
            violations += 1
        if scenario.expected_state == "RECOVERED" and not (
            transitions and decisions and typed_actions and outcomes and authoritative and recovered
        ):
            incomplete_audits += 1
        cases.append(
            {
                "name": scenario.name,
                "simulation_id": simulation_id,
                "provider_event_id": provider_event_id,
                "scenario": scenario.scenario,
                "amount_minor": scenario.amount_minor,
                "currency": "INR",
                "expected_state": scenario.expected_state,
                "observed_status": snapshot.get("status"),
                "observed_case_state": snapshot.get("case_state"),
                "policy_result": snapshot.get("policy_result"),
                "policy_reason_codes": snapshot.get("policy_reason_codes", []),
                "action_type": snapshot.get("action_type"),
                "action_status": snapshot.get("action_status"),
                "outcome_authoritative": authoritative,
                "verified_recovered_minor": recovered if authoritative else 0,
                "audit_counts": {
                    "transitions": len(transitions),
                    "decisions": len(decisions),
                    "actions": len(typed_actions),
                    "outcomes": len(outcomes),
                },
                "passed": _scenario_complete(scenario, snapshot),
            }
        )

    first_event_id = sessions[0][2]
    all_cases_passed = all(case["passed"] is True for case in cases)
    safety_passed = not any(
        (violations, unverified_counted, attempts_beyond_limit, incomplete_audits)
    )
    return {
        "schema_version": "1.0",
        "source": "SYNTHETIC",
        "evaluation_scope": "LIVE_LOCAL_INTEGRATION_BATCH",
        "simulation_disclosure": (
            "All records and recovered amounts are synthetic Test Mode evidence, not production "
            "merchant revenue."
        ),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "system_readiness": readiness,
        "held_out_contract": {
            "dataset_version": manifest.dataset_version,
            "content_hash": manifest.content_hash,
            "scenario_count": manifest.scenario_count,
            "execution_note": (
                "This live demo batch is representative integration evidence; it does not claim "
                "execution of all sealed held-out scenario oracles."
            ),
        },
        "boundaries_observed": [
            "authenticated simulation API",
            "signed durable webhook inbox",
            "Redis/Celery event dispatch",
            "normalization and recovery case state machine",
            "bounded case intelligence and deterministic policy",
            "durable action outbox and executor",
            "signed outcome verification",
            "PostgreSQL-backed dashboard audit reads",
        ],
        "batch": {
            "case_count": len(cases),
            "currency": "INR",
            "revenue_at_risk_minor": at_risk_minor,
            "verified_gross_recovered_minor": recovered_minor,
            "recovery_rate_basis_points": (
                recovered_minor * 10_000 // at_risk_minor if at_risk_minor else 0
            ),
            "recovered_case_count": sum(
                case["observed_case_state"] == "RECOVERED" for case in cases
            ),
            "human_review_case_count": sum(
                case["observed_case_state"] == "ESCALATED" for case in cases
            ),
            "deferred_case_count": sum(case["observed_case_state"] == "DEFERRED" for case in cases),
        },
        "idempotency": {
            "replayed_provider_event_id": duplicate_event_id,
            "original_provider_event_id": first_event_id,
            "same_logical_event": duplicate_event_id == first_event_id,
        },
        "safety_gates": {
            "policy_violations": violations,
            "unverified_amount_counted_as_recovered": unverified_counted,
            "actions_beyond_limits": attempts_beyond_limit,
            "recovered_cases_missing_audit_evidence": incomplete_audits,
            "status": "PASS" if safety_passed else "FAIL",
        },
        "cases": cases,
        "result": "PASS" if all_cases_passed and safety_passed else "FAIL",
    }


def _required_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise IntegratedEvaluationError(f"API response is missing {key}")
    return value
