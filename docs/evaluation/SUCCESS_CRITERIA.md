# Frozen Evaluation Success Criteria

**Status:** Pre-registered and frozen  
**Evaluation contract version:** `1.0.0`  
**Frozen on:** 2026-08-25  
**Applies to:** RevenueGuard MVP

These criteria are fixed before generating implementation results. Changes require an ADR that explains why the original criterion became invalid; results must be reported under both definitions when practical.

## Evaluation data

RevenueGuard will use:

1. Sanitized Razorpay Test Mode fixtures for integration correctness.
2. A webhook replay harness for delivery and failure behavior.
3. Seeded synthetic merchant portfolios for statistical strategy evaluation.

Every synthetic record and report must contain `source = SYNTHETIC`, generator version, configuration hash, and seed. Hidden outcome probabilities are available only to the simulator.

## Frozen dataset protocol

- Split by customer and chronological boundary: 70% training, 15% validation, 15% held-out test.
- Freeze held-out case IDs and generator configuration before strategy/model tuning.
- Include an out-of-distribution incident set with at least one unseen payment-degradation pattern.
- Run stochastic evaluation over at least 10 fixed seeds.
- Report aggregate raw counts, mean, standard deviation, and a 95% uncertainty interval for business outcomes.
- Never tune thresholds or prompts against the held-out test set.

## Baselines

The same held-out cases and simulator draws must evaluate:

1. `NO_ACTION`
2. `IMMEDIATE_STATIC_RETRY`
3. `FIXED_DELAY_RETRY`
4. `RULES_ONLY`
5. `CASE_ONLY` without portfolio coordination
6. `REVENUEGUARD_FULL`

## Primary success criterion

`REVENUEGUARD_FULL` must produce higher mean verified net recovered revenue than the best non-RevenueGuard static baseline (`IMMEDIATE_STATIC_RETRY` or `FIXED_DELAY_RETRY`) on the frozen held-out set.

The report must include absolute minor-unit values, incremental value, percentage improvement, uncertainty intervals, and per-seed outcomes. A percentage alone is insufficient.

## Hard safety gates

All controlled replay and evaluation runs must satisfy:

```text
policy violations                          = 0
duplicate external business effects        = 0
unverified amount counted as recovered     = 0
actions beyond retry/contact limits        = 0
cross-merchant data access                 = 0
accepted valid events silently lost        = 0
```

Failure of any hard safety gate fails the release regardless of recovered-revenue improvement.

## Integration correctness gates

- Five deliveries of one valid provider event produce one processed logical event.
- Invalid signatures produce no normalized event or recovery action.
- A crash after inbox commit does not lose the accepted event.
- A crash around execution does not produce a duplicate business effect.
- An ambiguous provider response enters `UNKNOWN` and suppresses equivalent execution.
- Later authoritative evidence reconciles `UNKNOWN` correctly.
- Only authoritative success contributes to recovered revenue.
- Out-of-order events do not regress terminal provider or case truth.

## Model metrics

Report:

- Precision, recall, and F1.
- ROC-AUC and PR-AUC.
- Brier score and calibration curve/error.
- Results by failure category, amount band, payment method, and merchant segment.
- Performance change on the out-of-distribution incident set.

No fixed minimum model score is pre-registered because business utility and calibration matter more than classification accuracy alone. Model complexity is justified only if it improves held-out net recovery or safety over an interpretable calibrated baseline.

## Business and operational metrics

Report at minimum:

- Cases evaluated and revenue at risk.
- Verified recovered cases.
- Verified gross and net recovered revenue.
- Recovery cost and cost per recovered rupee.
- Actions attempted, deferred, skipped, stopped, and escalated.
- Customer contacts and unnecessary interventions.
- Policy blocks and human approvals/rejections.
- Duplicate attempts prevented and duplicate effects.
- Unknown outcomes, maximum age, and reconciliation rate.
- Incident precision, recall, false positives, time to detect, and time to resolve.
- Event-to-safe-decision p50 and p95 latency.

## Required ablations

- Full system minus portfolio intelligence.
- Full system minus ML/LLM strategy ranking, retaining deterministic rules.
- Full system minus cross-workflow customer coordination.

An ablation that does not improve results must be disclosed; the corresponding complexity should be removed or explicitly justified for another measured purpose.

## Claim policy

- All synthetic results must be labelled as simulation results.
- Razorpay Test Mode validates integration behavior, not production recovery performance.
- Do not claim regulatory certification, live merchant outcomes, or guaranteed recovery.
- Report regressions, subgroup weaknesses, failed seeds, and unresolved unknown outcomes.
- Preserve machine-readable evaluation artifacts so every headline number can be reproduced.
