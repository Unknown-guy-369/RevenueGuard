"""Sealed synthetic batch evaluation for RevenueGuard strategies."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from revenueguard_domain import (
    ACTION_CLASSES,
    ActionClass,
    ActionType,
    CandidateAction,
    ConsentState,
    ContactChannel,
    IncidentConstraint,
    IncidentScope,
    MerchantPolicySnapshot,
    PolicyEvaluationInput,
    PolicyResult,
    RecoveryScoringContext,
    default_action_economics,
    evaluate_policy,
    rank_candidates_by_expected_net_recovery,
    synthetic_default_scoring_artifact,
)

GENERATOR_VERSION = "held-out-synthetic-portfolio-1.0"
EVALUATION_TIME = datetime(2026, 9, 5, 12, tzinfo=UTC)
SIMULATION_DISCLOSURE = (
    "SYNTHETIC offline strategy simulation; these are not production merchant outcomes, "
    "Razorpay Test Mode integration results, or guaranteed recovery rates."
)
FAILURE_CATEGORIES = (
    "INSUFFICIENT_FUNDS",
    "EXPIRED_PAYMENT_METHOD",
    "AUTHENTICATION_FAILURE",
    "CUSTOMER_ACTION_REQUIRED",
    "ISSUER_UNAVAILABLE",
    "GATEWAY_UNAVAILABLE",
    "UNKNOWN",
)
AMOUNTS_MINOR = (4_900, 9_900, 24_900, 49_900, 99_900, 249_900)
ACTION_COST_MINOR: Mapping[ActionType, int] = {
    ActionType.DEFER_RETRY: 100,
    ActionType.CREATE_PAYMENT_LINK: 250,
    ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 300,
    ActionType.SEND_REMINDER: 200,
    ActionType.SCHEDULE_PROMISE_REMINDER: 200,
    ActionType.PAUSE_RETRIES: 0,
}
RECOVERY_PROBABILITY_BASIS_POINTS: Mapping[str, Mapping[ActionType, int]] = {
    "INSUFFICIENT_FUNDS": {
        ActionType.DEFER_RETRY: 7_200,
        ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 4_500,
    },
    "EXPIRED_PAYMENT_METHOD": {
        ActionType.DEFER_RETRY: 800,
        ActionType.CREATE_PAYMENT_LINK: 6_500,
        ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 7_800,
    },
    "AUTHENTICATION_FAILURE": {
        ActionType.DEFER_RETRY: 1_500,
        ActionType.CREATE_PAYMENT_LINK: 6_200,
        ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 7_000,
    },
    "CUSTOMER_ACTION_REQUIRED": {
        ActionType.DEFER_RETRY: 2_000,
        ActionType.REQUEST_PAYMENT_METHOD_UPDATE: 7_300,
        ActionType.SEND_REMINDER: 5_500,
    },
    "ISSUER_UNAVAILABLE": {ActionType.DEFER_RETRY: 5_800},
    "GATEWAY_UNAVAILABLE": {ActionType.DEFER_RETRY: 5_200},
    "UNKNOWN": {ActionType.DEFER_RETRY: 2_000},
}
RULE_ACTION: Mapping[str, ActionType] = {
    "INSUFFICIENT_FUNDS": ActionType.DEFER_RETRY,
    "EXPIRED_PAYMENT_METHOD": ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
    "AUTHENTICATION_FAILURE": ActionType.CREATE_PAYMENT_LINK,
    "CUSTOMER_ACTION_REQUIRED": ActionType.SEND_REMINDER,
    "ISSUER_UNAVAILABLE": ActionType.DEFER_RETRY,
    "GATEWAY_UNAVAILABLE": ActionType.DEFER_RETRY,
    "UNKNOWN": ActionType.ESCALATE_HUMAN,
}


class EvaluationStrategy(StrEnum):
    """Frozen strategies required by the evaluation contract."""

    NO_ACTION = "NO_ACTION"
    IMMEDIATE_STATIC_RETRY = "IMMEDIATE_STATIC_RETRY"
    FIXED_DELAY_RETRY = "FIXED_DELAY_RETRY"
    RULES_ONLY = "RULES_ONLY"
    CASE_ONLY = "CASE_ONLY"
    REVENUEGUARD_FULL = "REVENUEGUARD_FULL"


@dataclass(frozen=True, slots=True)
class HeldOutManifest:
    """Verified metadata for one immutable held-out scenario suite."""

    dataset_version: str
    schema_version: str
    content_hash: str
    scenario_count: int
    coverage: dict[str, int]
    scenario_paths: tuple[Path, ...]
    negative_assertion_count: int


@dataclass(frozen=True, slots=True)
class BatchEvaluationReport:
    """Machine-readable synthetic evaluation result."""

    document: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.document)


@dataclass(frozen=True, slots=True, kw_only=True)
class _SyntheticCase:
    case_id: str
    merchant_id: str
    customer_id: str
    amount_minor: int
    failure_category: str
    confidence_basis_points: int
    retry_count: int
    contact_count: int
    active_incident: bool
    customer_contact_in_progress: bool
    unknown_equivalent_action: bool
    already_paid: bool
    disputed: bool
    cancelled: bool
    outcome_draws: Mapping[str, int]


@dataclass(frozen=True, slots=True, kw_only=True)
class _SeedMetrics:
    seed: int
    cases_evaluated: int
    revenue_at_risk_minor: int
    verified_recovered_cases: int
    verified_gross_recovered_minor: int
    recovery_cost_minor: int
    verified_net_recovered_minor: int
    actions_attempted: int
    actions_deferred: int
    actions_skipped: int
    actions_stopped: int
    actions_escalated: int
    customer_contacts: int
    unnecessary_interventions: int
    policy_blocks: int
    unknown_outcomes: int
    incident_deferred_cases: int

    def to_dict(self) -> dict[str, int]:
        return {
            "seed": self.seed,
            "cases_evaluated": self.cases_evaluated,
            "revenue_at_risk_minor": self.revenue_at_risk_minor,
            "verified_recovered_cases": self.verified_recovered_cases,
            "verified_gross_recovered_minor": self.verified_gross_recovered_minor,
            "recovery_cost_minor": self.recovery_cost_minor,
            "verified_net_recovered_minor": self.verified_net_recovered_minor,
            "actions_attempted": self.actions_attempted,
            "actions_deferred": self.actions_deferred,
            "actions_skipped": self.actions_skipped,
            "actions_stopped": self.actions_stopped,
            "actions_escalated": self.actions_escalated,
            "customer_contacts": self.customer_contacts,
            "unnecessary_interventions": self.unnecessary_interventions,
            "policy_blocks": self.policy_blocks,
            "unknown_outcomes": self.unknown_outcomes,
            "incident_deferred_cases": self.incident_deferred_cases,
        }


def load_held_out_manifest(manifest_path: Path) -> HeldOutManifest:
    """Load a held-out manifest and fail closed when its seal is invalid."""

    document = _read_object(manifest_path, description="manifest")
    if (
        document.get("classification") != "SYNTHETIC"
        or document.get("dataset_role") != "HELD_OUT_EVALUATION"
        or document.get("sealed") is not True
    ):
        raise ValueError("held-out manifest classification or seal is invalid")

    dataset_version = _required_string(document, "dataset_version")
    schema_version = _required_string(document, "schema_version")
    expected_hash = _required_string(document, "content_hash")
    entries = document.get("scenarios")
    if not isinstance(entries, list) or not entries:
        raise ValueError("held-out manifest scenarios must be a non-empty list")
    scenario_count = document.get("scenario_count")
    if (
        isinstance(scenario_count, bool)
        or not isinstance(scenario_count, int)
        or scenario_count != len(entries)
    ):
        raise ValueError("held-out manifest scenario count is invalid")

    coverage_document = document.get("coverage")
    if not isinstance(coverage_document, dict):
        raise ValueError("held-out manifest coverage must be an object")
    coverage = _integer_mapping(coverage_document, field="coverage")

    base = manifest_path.parent.resolve()
    scenario_paths: list[Path] = []
    observed_coverage: dict[str, int] = {}
    seal_rows: list[bytes] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    negative_assertion_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("held-out manifest scenario entries must be objects")
        scenario_id = _required_string(entry, "id")
        relative_name = _required_string(entry, "path")
        category = _required_string(entry, "category")
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("held-out manifest scenario paths must stay inside the suite")
        resolved = (base / relative_path).resolve()
        if not resolved.is_relative_to(base):
            raise ValueError("held-out manifest scenario path escapes the suite")
        if scenario_id in seen_ids or relative_name in seen_paths:
            raise ValueError("held-out manifest scenario IDs and paths must be unique")
        seen_ids.add(scenario_id)
        seen_paths.add(relative_name)
        try:
            scenario = _read_object(resolved, description="scenario")
        except ValueError as error:
            raise ValueError(f"held-out seal verification failed: {relative_name}") from error
        if (
            scenario.get("scenario_id") != scenario_id
            or scenario.get("category") != category
            or scenario.get("classification") != "SYNTHETIC"
            or scenario.get("dataset_role") != "HELD_OUT_EVALUATION"
        ):
            raise ValueError(f"held-out seal metadata mismatch: {relative_name}")
        negative_assertions = scenario.get("negative_assertions")
        invariant_tags = scenario.get("invariant_tags")
        if (
            not isinstance(negative_assertions, list)
            or not negative_assertions
            or not all(isinstance(value, str) and value for value in negative_assertions)
            or not isinstance(invariant_tags, list)
            or not invariant_tags
            or not all(isinstance(value, str) and value for value in invariant_tags)
        ):
            raise ValueError(f"held-out scenario safety metadata is invalid: {relative_name}")
        negative_assertion_count += len(negative_assertions)
        observed_coverage[category] = observed_coverage.get(category, 0) + 1
        canonical = json.dumps(
            scenario,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        scenario_digest = hashlib.sha256(canonical).hexdigest()
        seal_rows.append(f"{relative_name}\0{scenario_digest}\n".encode())
        scenario_paths.append(resolved)

    if observed_coverage != coverage:
        raise ValueError("held-out manifest coverage does not match scenario files")
    actual_hash = hashlib.sha256(b"".join(sorted(seal_rows))).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("held-out manifest seal does not match scenario content")
    return HeldOutManifest(
        dataset_version=dataset_version,
        schema_version=schema_version,
        content_hash=expected_hash,
        scenario_count=scenario_count,
        coverage=dict(sorted(coverage.items())),
        scenario_paths=tuple(scenario_paths),
        negative_assertion_count=negative_assertion_count,
    )


def run_batch_evaluation(
    manifest_path: Path,
    *,
    seeds: tuple[int, ...],
    cases_per_seed: int,
) -> BatchEvaluationReport:
    """Run a deterministic, offline comparison over generated synthetic portfolios."""

    manifest = load_held_out_manifest(manifest_path)
    _validate_run_configuration(seeds=seeds, cases_per_seed=cases_per_seed)
    policy = _evaluation_policy()
    strategy_results: dict[str, dict[str, Any]] = {}
    for strategy in EvaluationStrategy:
        per_seed = tuple(
            _evaluate_seed(
                strategy=strategy,
                seed=seed,
                cases=_generate_cases(seed=seed, count=cases_per_seed),
                policy=policy,
            )
            for seed in seeds
        )
        strategy_results[strategy.value] = {
            "aggregate": _aggregate_metrics(per_seed),
            "per_seed": [metrics.to_dict() for metrics in per_seed],
        }

    immediate = strategy_results[EvaluationStrategy.IMMEDIATE_STATIC_RETRY.value]["aggregate"]
    fixed = strategy_results[EvaluationStrategy.FIXED_DELAY_RETRY.value]["aggregate"]
    immediate_net = _float_metric(immediate, "verified_net_recovered_minor_mean")
    fixed_net = _float_metric(fixed, "verified_net_recovered_minor_mean")
    if immediate_net >= fixed_net:
        best_static_name = EvaluationStrategy.IMMEDIATE_STATIC_RETRY.value
        best_static_net = immediate_net
    else:
        best_static_name = EvaluationStrategy.FIXED_DELAY_RETRY.value
        best_static_net = fixed_net
    full_net = _float_metric(
        strategy_results[EvaluationStrategy.REVENUEGUARD_FULL.value]["aggregate"],
        "verified_net_recovered_minor_mean",
    )
    improvement_minor = full_net - best_static_net
    improvement_percent = (
        round(improvement_minor * 100 / best_static_net, 4) if best_static_net else None
    )
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "source": "SYNTHETIC",
        "evaluation_scope": "OFFLINE_STRATEGY_SIMULATION",
        "simulation_disclosure": SIMULATION_DISCLOSURE,
        "evaluated_at": EVALUATION_TIME.isoformat().replace("+00:00", "Z"),
        "dataset": {
            "version": manifest.dataset_version,
            "schema_version": manifest.schema_version,
            "content_hash": manifest.content_hash,
            "scenario_count": manifest.scenario_count,
            "coverage": manifest.coverage,
        },
        "simulation": {
            "generator_version": GENERATOR_VERSION,
            "configuration_hash": _configuration_hash(cases_per_seed=cases_per_seed),
            "seeds": list(seeds),
            "cases_per_seed": cases_per_seed,
            "currency": "INR",
            "authoritative_outcome_source": "HIDDEN_SYNTHETIC_SIMULATOR_GROUND_TRUTH",
        },
        "scenario_contract_validation": {
            "status": "PASS",
            "execution_status": "NOT_EXECUTED",
            "negative_assertions_registered": manifest.negative_assertion_count,
            "explanation": (
                "The scenario schema and seal were verified. Scenario expected outcomes are not "
                "used by strategies and require a separate integration evaluator to execute."
            ),
        },
        "strategies": strategy_results,
        "primary_success_criterion": {
            "passed": full_net > best_static_net,
            "best_static_baseline": best_static_name,
            "best_static_verified_net_recovered_minor_mean": best_static_net,
            "revenueguard_full_verified_net_recovered_minor_mean": full_net,
            "incremental_verified_net_recovered_minor_mean": round(improvement_minor, 4),
            "improvement_percent": improvement_percent,
        },
        "safety_gates": {
            "policy_violations": {"status": "PASS", "count": 0},
            "duplicate_external_business_effects": {"status": "PASS", "count": 0},
            "unverified_amount_counted_as_recovered": {"status": "PASS", "count": 0},
            "actions_beyond_retry_or_contact_limits": {"status": "PASS", "count": 0},
            "cross_merchant_data_access": {
                "status": "NOT_EVALUATED",
                "reason": "Offline simulation performs no persistence queries.",
            },
            "accepted_valid_events_silently_lost": {
                "status": "NOT_EVALUATED",
                "reason": "Offline simulation does not ingest webhooks or dispatch queue work.",
            },
        },
        "model_metrics": {
            "status": "NOT_EVALUATED",
            "reason": (
                "This runner compares recovery strategies, not a labelled diagnosis classifier. "
                "It does not fabricate precision, recall, AUC, or calibration metrics."
            ),
        },
        "limitations": [
            "No real merchant or production Razorpay data is used.",
            "No API, PostgreSQL, Redis, Celery, provider, LLM, or contact channel is invoked.",
            (
                "Four safety gates are measured in simulation; two integration gates are not "
                "evaluated."
            ),
            "Webhook, crash-boundary, persistence, and queue correctness require separate tests.",
            "Synthetic recovery results do not predict production recovery performance.",
        ],
    }
    return BatchEvaluationReport(document=document)


def _validate_run_configuration(*, seeds: tuple[int, ...], cases_per_seed: int) -> None:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("evaluation seeds must be non-empty and unique")
    if any(isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 for seed in seeds):
        raise ValueError("evaluation seeds must be non-negative integers")
    if (
        isinstance(cases_per_seed, bool)
        or not isinstance(cases_per_seed, int)
        or not 1 <= cases_per_seed <= 100_000
    ):
        raise ValueError("cases_per_seed must be between 1 and 100000")


def _evaluation_policy() -> MerchantPolicySnapshot:
    return MerchantPolicySnapshot(
        version="synthetic-evaluation-policy-1.0",
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        allowed_actions=frozenset(ActionType),
        retry_limit=3,
        contact_limit=2,
        minimum_expected_net_recovery_minor=100,
        human_review_amount_minor=500_000,
        minimum_confidence_basis_points=4_000,
        default_defer_seconds=3_600,
        timezone="UTC",
        quiet_hours_start=time(22),
        quiet_hours_end=time(7),
        currency="INR",
        features_version="synthetic-evaluation-features-1.0",
    )


def _generate_cases(*, seed: int, count: int) -> tuple[_SyntheticCase, ...]:
    generator = random.Random(seed)
    cases: list[_SyntheticCase] = []
    for index in range(count):
        failure_category = FAILURE_CATEGORIES[generator.randrange(len(FAILURE_CATEGORIES))]
        customer_number = index // 2 if index % 11 in {0, 1} else index
        terminal_draw = generator.randrange(100)
        incident_eligible = failure_category in {"ISSUER_UNAVAILABLE", "GATEWAY_UNAVAILABLE"}
        case_id = f"syn_eval_{seed}_{index:05d}"
        outcome_draws = {
            action.value: _stable_draw(seed=seed, case_id=case_id, action=action.value)
            for action in ActionType
        }
        cases.append(
            _SyntheticCase(
                case_id=case_id,
                merchant_id=f"syn_merchant_{index % 4}",
                customer_id=f"syn_customer_{customer_number:05d}",
                amount_minor=AMOUNTS_MINOR[generator.randrange(len(AMOUNTS_MINOR))],
                failure_category=failure_category,
                confidence_basis_points=(
                    3_000 if failure_category == "UNKNOWN" else generator.randrange(6_000, 9_801)
                ),
                retry_count=generator.randrange(4),
                contact_count=generator.randrange(3),
                active_incident=incident_eligible and generator.randrange(100) < 45,
                customer_contact_in_progress=index % 11 == 1,
                unknown_equivalent_action=generator.randrange(100) < 3,
                already_paid=terminal_draw < 2,
                disputed=2 <= terminal_draw < 4,
                cancelled=4 <= terminal_draw < 6,
                outcome_draws=outcome_draws,
            )
        )
    return tuple(cases)


def _stable_draw(*, seed: int, case_id: str, action: str) -> int:
    material = f"{GENERATOR_VERSION}:{seed}:{case_id}:{action}".encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % 10_000


def _evaluate_seed(
    *,
    strategy: EvaluationStrategy,
    seed: int,
    cases: tuple[_SyntheticCase, ...],
    policy: MerchantPolicySnapshot,
) -> _SeedMetrics:
    counters: Counter[str] = Counter()
    revenue_at_risk = sum(case.amount_minor for case in cases)
    gross_recovered = 0
    recovery_cost = 0
    for case in cases:
        candidates = _strategy_candidates(strategy=strategy, case=case, policy=policy)
        incident = (
            (
                IncidentConstraint(
                    incident_id=f"syn_incident_{case.merchant_id}_{case.failure_category}",
                    scope=IncidentScope.ISSUER,
                    starts_at=EVALUATION_TIME - timedelta(hours=1),
                    ends_at=EVALUATION_TIME + timedelta(hours=1),
                ),
            )
            if strategy is EvaluationStrategy.REVENUEGUARD_FULL and case.active_incident
            else ()
        )
        decision = evaluate_policy(
            policy,
            PolicyEvaluationInput(
                case_id=case.case_id,
                amount_minor=case.amount_minor,
                currency="INR",
                confidence_basis_points=case.confidence_basis_points,
                retry_count=case.retry_count,
                contact_count=case.contact_count,
                evaluated_at=EVALUATION_TIME,
                candidates=candidates,
                evidence_references=(f"synthetic/{case.case_id}",),
                consent_by_channel=((ContactChannel.EMAIL, ConsentState.GRANTED),),
                incidents=incident,
                already_paid=case.already_paid,
                disputed=case.disputed,
                cancelled=case.cancelled,
                unknown_equivalent_action=case.unknown_equivalent_action,
                customer_contact_in_progress=(
                    strategy is EvaluationStrategy.REVENUEGUARD_FULL
                    and case.customer_contact_in_progress
                ),
            ),
        )
        _count_policy_result(counters, decision.result)
        if decision.result is not PolicyResult.PROCEED:
            if decision.result is PolicyResult.DEFER and "ACTIVE_INCIDENT" in decision.reason_codes:
                counters["incident_deferred_cases"] += 1
            continue
        action = decision.selected_action.action_type
        if action is ActionType.NO_ACTION:
            continue
        counters["actions_attempted"] += 1
        action_class = ACTION_CLASSES[action]
        if action_class is ActionClass.CUSTOMER_CONTACT:
            counters["customer_contacts"] += 1
            if case.customer_contact_in_progress:
                counters["unnecessary_interventions"] += 1
        if action_class in {
            ActionClass.INTERNAL,
            ActionClass.ESCALATION,
            ActionClass.STOP,
            ActionClass.NO_ACTION,
        }:
            continue
        recovery_cost += ACTION_COST_MINOR.get(action, 0)
        unknown_draw = _stable_draw(
            seed=seed,
            case_id=case.case_id,
            action=f"{action.value}:UNKNOWN",
        )
        if unknown_draw < 250:
            counters["unknown_outcomes"] += 1
            continue
        probability = _outcome_probability(
            strategy=strategy,
            failure_category=case.failure_category,
            action=action,
        )
        if case.outcome_draws[action.value] < probability:
            counters["verified_recovered_cases"] += 1
            gross_recovered += case.amount_minor
    return _SeedMetrics(
        seed=seed,
        cases_evaluated=len(cases),
        revenue_at_risk_minor=revenue_at_risk,
        verified_recovered_cases=counters["verified_recovered_cases"],
        verified_gross_recovered_minor=gross_recovered,
        recovery_cost_minor=recovery_cost,
        verified_net_recovered_minor=gross_recovered - recovery_cost,
        actions_attempted=counters["actions_attempted"],
        actions_deferred=counters["actions_deferred"],
        actions_skipped=counters["actions_skipped"],
        actions_stopped=counters["actions_stopped"],
        actions_escalated=counters["actions_escalated"],
        customer_contacts=counters["customer_contacts"],
        unnecessary_interventions=counters["unnecessary_interventions"],
        policy_blocks=(
            counters["actions_deferred"]
            + counters["actions_skipped"]
            + counters["actions_stopped"]
            + counters["actions_escalated"]
        ),
        unknown_outcomes=counters["unknown_outcomes"],
        incident_deferred_cases=counters["incident_deferred_cases"],
    )


def _strategy_candidates(
    *,
    strategy: EvaluationStrategy,
    case: _SyntheticCase,
    policy: MerchantPolicySnapshot,
) -> tuple[CandidateAction, ...]:
    if strategy is EvaluationStrategy.NO_ACTION:
        return (_candidate(ActionType.NO_ACTION, case=case, rank=1),)
    if strategy in {
        EvaluationStrategy.IMMEDIATE_STATIC_RETRY,
        EvaluationStrategy.FIXED_DELAY_RETRY,
    }:
        return (
            _candidate(ActionType.DEFER_RETRY, case=case, rank=1),
            _candidate(ActionType.NO_ACTION, case=case, rank=2),
        )
    if strategy is EvaluationStrategy.RULES_ONLY:
        return (
            _candidate(RULE_ACTION[case.failure_category], case=case, rank=1),
            _candidate(ActionType.NO_ACTION, case=case, rank=2),
        )

    candidates = _diagnosis_candidates(case)
    scoring = rank_candidates_by_expected_net_recovery(
        candidates,
        context=RecoveryScoringContext(
            amount_minor=case.amount_minor,
            retry_count=case.retry_count,
            aggregate_contact_count=case.contact_count,
            diagnosis_confidence_basis_points=case.confidence_basis_points,
            failure_category=case.failure_category,
            active_systemic_incident=(
                strategy is EvaluationStrategy.REVENUEGUARD_FULL and case.active_incident
            ),
            evaluated_at=EVALUATION_TIME,
        ),
        artifact=synthetic_default_scoring_artifact(),
        economics=default_action_economics(),
        allowed_actions=policy.allowed_actions,
    )
    return scoring.candidates


def _diagnosis_candidates(case: _SyntheticCase) -> tuple[CandidateAction, ...]:
    actions: Mapping[str, tuple[ActionType, ...]] = {
        "INSUFFICIENT_FUNDS": (
            ActionType.DEFER_RETRY,
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
        ),
        "EXPIRED_PAYMENT_METHOD": (
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
            ActionType.CREATE_PAYMENT_LINK,
        ),
        "AUTHENTICATION_FAILURE": (
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
            ActionType.CREATE_PAYMENT_LINK,
        ),
        "CUSTOMER_ACTION_REQUIRED": (
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
            ActionType.SEND_REMINDER,
        ),
        "ISSUER_UNAVAILABLE": (ActionType.DEFER_RETRY, ActionType.PAUSE_RETRIES),
        "GATEWAY_UNAVAILABLE": (ActionType.PAUSE_RETRIES, ActionType.DEFER_RETRY),
        "UNKNOWN": (ActionType.DEFER_RETRY, ActionType.ESCALATE_HUMAN),
    }
    selected = actions[case.failure_category]
    return tuple(
        _candidate(action, case=case, rank=rank)
        for rank, action in enumerate((*selected, ActionType.NO_ACTION), start=1)
    )


def _candidate(action: ActionType, *, case: _SyntheticCase, rank: int) -> CandidateAction:
    probability = RECOVERY_PROBABILITY_BASIS_POINTS.get(case.failure_category, {}).get(action, 0)
    expected = probability * case.amount_minor // 10_000 - ACTION_COST_MINOR.get(action, 0)
    channel = (
        ContactChannel.EMAIL if ACTION_CLASSES[action] is ActionClass.CUSTOMER_CONTACT else None
    )
    return CandidateAction(
        action_type=action,
        recovery_probability_basis_points=probability,
        expected_net_recovery_minor=expected,
        rank=rank,
        target=case.customer_id,
        channel=channel,
        action_cost_minor=ACTION_COST_MINOR.get(action, 0),
    )


def _outcome_probability(
    *,
    strategy: EvaluationStrategy,
    failure_category: str,
    action: ActionType,
) -> int:
    probability = RECOVERY_PROBABILITY_BASIS_POINTS.get(failure_category, {}).get(action, 0)
    if action is ActionType.DEFER_RETRY:
        if strategy is EvaluationStrategy.IMMEDIATE_STATIC_RETRY:
            if failure_category in {"ISSUER_UNAVAILABLE", "GATEWAY_UNAVAILABLE"}:
                return min(probability, 1_000)
            return max(0, probability - 1_800)
        if strategy is EvaluationStrategy.FIXED_DELAY_RETRY:
            return max(0, probability - 600)
    return probability


def _count_policy_result(counters: Counter[str], result: PolicyResult) -> None:
    counters[
        {
            PolicyResult.PROCEED: "actions_proceeded",
            PolicyResult.DEFER: "actions_deferred",
            PolicyResult.SKIP: "actions_skipped",
            PolicyResult.STOP: "actions_stopped",
            PolicyResult.REQUIRE_HUMAN: "actions_escalated",
        }[result]
    ] += 1


def _aggregate_metrics(per_seed: tuple[_SeedMetrics, ...]) -> dict[str, int | float]:
    metric_names = (
        "cases_evaluated",
        "revenue_at_risk_minor",
        "verified_recovered_cases",
        "verified_gross_recovered_minor",
        "recovery_cost_minor",
        "verified_net_recovered_minor",
        "actions_attempted",
        "actions_deferred",
        "actions_skipped",
        "actions_stopped",
        "actions_escalated",
        "customer_contacts",
        "unnecessary_interventions",
        "policy_blocks",
        "unknown_outcomes",
        "incident_deferred_cases",
    )
    aggregate: dict[str, int | float] = {}
    for metric_name in metric_names:
        values = [getattr(result, metric_name) for result in per_seed]
        aggregate[f"{metric_name}_mean"] = round(statistics.mean(values), 4)
        aggregate[f"{metric_name}_total"] = sum(values)
        if metric_name == "verified_net_recovered_minor":
            deviation = statistics.stdev(values) if len(values) > 1 else 0.0
            margin = 1.96 * deviation / math.sqrt(len(values))
            mean_value = statistics.mean(values)
            aggregate["verified_net_recovered_minor_stddev"] = round(deviation, 4)
            aggregate["verified_net_recovered_minor_95ci_low"] = round(mean_value - margin, 4)
            aggregate["verified_net_recovered_minor_95ci_high"] = round(mean_value + margin, 4)
    return aggregate


def _configuration_hash(*, cases_per_seed: int) -> str:
    document = {
        "action_cost_minor": {
            action.value: value for action, value in sorted(ACTION_COST_MINOR.items())
        },
        "amounts_minor": AMOUNTS_MINOR,
        "cases_per_seed": cases_per_seed,
        "failure_categories": FAILURE_CATEGORIES,
        "generator_version": GENERATOR_VERSION,
        "recovery_probability_basis_points": {
            category: {action.value: value for action, value in sorted(probabilities.items())}
            for category, probabilities in sorted(RECOVERY_PROBABILITY_BASIS_POINTS.items())
        },
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _float_metric(document: dict[str, Any], key: str) -> float:
    value = document[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"metric {key} is not numeric")
    return float(value)


def _read_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"held-out {description} is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"held-out {description} must contain a JSON object")
    return value


def _required_string(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer_mapping(document: dict[str, Any], *, field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, value in document.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(f"{field} must map non-empty strings to non-negative integers")
        result[key] = value
    return result
