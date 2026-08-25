# Phase 0 Product Contract

**Status:** Frozen for MVP implementation  
**Contract version:** `1.0.0`  
**Frozen on:** 2026-08-25  
**Project:** RevenueGuard

This document freezes the MVP scope, actors, authority boundaries, recovery playbooks, state semantics, and explicitly deferred capabilities. Changes require a documented architecture decision and corresponding contract/test updates.

## Objective

RevenueGuard detects revenue at risk, creates durable recovery cases, recommends safe interventions, applies deterministic merchant policy, executes approved actions idempotently, verifies provider outcomes, and measures confirmed money recovered.

The MVP proves one complete control loop:

```text
detect
→ diagnose
→ score
→ recommend
→ authorize
→ execute
→ verify
→ recover / adapt / defer / escalate / stop
```

## Actors

| Actor | Responsibilities | Explicitly prohibited |
|---|---|---|
| Merchant operator | Configure merchant recovery policy, inspect cases/incidents/metrics, manage allowed channels | Bypass tenant scope, rewrite historical receipts, mark unverified money recovered |
| Human approver | Approve or reject a sensitive proposed action with rationale | Approve across merchants, create a different action implicitly, bypass revalidation at execution time |
| Recovery worker | Consume durable work, advance cases, persist decisions, schedule and reconcile actions | Invent provider state, exceed limits, execute an action without an authorized outbox record |
| Customer contact adapter | Deliver a pre-authorized communication and return provider evidence | Select channel/message/action autonomously, contact without consent/policy approval |
| Case recovery graph | Diagnose, retrieve read-only evidence, score and recommend typed candidate actions | Directly execute Razorpay/contact actions, mutate financial truth, override policy |
| Portfolio controller | Detect systemic degradation, create incident constraints, coordinate cases/customers | Mark individual payments recovered, cross tenant boundaries |
| Razorpay Test Mode | External payment provider and authoritative source for supported test outcomes | Treated as infallible or as the internal workflow source of truth |

## Authority matrix

| Capability | Agent graph | Policy engine | Human approver | Executor | Outcome verifier |
|---|---:|---:|---:|---:|---:|
| Read committed case evidence | Yes | Yes | Yes | Limited | Yes |
| Recommend an action | Yes | No | No | No | No |
| Authorize an action | No | Yes | Conditional approval only | No | No |
| Execute provider action | No | No | No | Yes, from outbox only | No |
| Mark provider outcome | No | No | No | Provisional only | Yes |
| Count recovered money | No | No | No | No | Yes, authoritative success only |
| Override deterministic stop | No | No | No | No | No |

## Canonical contracts

Phase 0 defines five versioned contracts under `docs/contracts/v1/`:

- `RevenueRiskEvent`
- `RecoveryCase`
- `DecisionReceipt`
- `RecoveryAction`
- `VerifiedOutcome`

All contracts include `schema_version = "1.0"`. Money is represented as a non-negative integer in minor units plus a three-letter uppercase ISO currency code. Financial floating point values are forbidden.

## Recovery case states

| State | Meaning |
|---|---|
| `DETECTED` | A valid revenue-risk event has created or correlated to a case |
| `DIAGNOSING` | Committed evidence is being classified |
| `DECISION_PENDING` | Candidate strategies are being created or reconsidered |
| `POLICY_CHECK` | A recommendation is undergoing deterministic authorization |
| `READY` | An action is authorized and ready to enter execution |
| `EXECUTING` | The outbox action is being attempted |
| `VERIFYING` | Provider evidence is being reconciled |
| `UNKNOWN` | The action may have succeeded; equivalent execution is blocked |
| `DEFERRED` | The case has a durable future re-evaluation time or condition |
| `ESCALATED` | The case awaits or has exceeded human review/reconciliation |
| `RECOVERED` | Authoritative evidence confirms recovered money |
| `STOPPED` | Automation has terminated without a verified recovery |

`RECOVERED` and `STOPPED` are terminal for the MVP. Allowed transitions are machine-readable in `docs/contracts/v1/case-state-machine.json`.

## Policy decisions

Every evaluated recommendation produces exactly one result:

- `PROCEED`: authorize creation of the specified outbox action.
- `DEFER`: persist a future re-evaluation time or condition.
- `SKIP`: reject this candidate while allowing another candidate.
- `STOP`: terminate incompatible automated recovery.
- `REQUIRE_HUMAN`: pause and create a scoped approval request.

Policy decisions are deterministic for the same immutable input and policy version. Approval does not bypass final policy re-evaluation immediately before execution.

## Action and outcome semantics

The Phase 0 action contract recognizes:

```text
PENDING
SUCCEEDED
FAILED
UNKNOWN
```

- `PENDING` means an authorized action has no terminal verified result.
- `SUCCEEDED` means authoritative evidence confirms the intended provider effect.
- `FAILED` means authoritative evidence confirms failure.
- `UNKNOWN` means the provider effect is ambiguous; equivalent actions are blocked until reconciliation.

Only authoritative `SUCCEEDED` outcomes may contain a positive `recovered_amount_minor` and contribute to recovered-revenue metrics.

## Frozen core playbooks

### Failed subscription recovery

Supported MVP strategies:

- Defer retry.
- Request payment-method update.
- Create a Razorpay Test Mode payment link.
- Send a policy-approved reminder through a simulated/test adapter.
- Require human review.
- Stop.

Required failure families:

- Insufficient funds.
- Expired payment method.
- Authentication failure.
- Issuer/gateway unavailable.
- Unknown/unsupported failure.

### Payment degradation

Supported MVP behavior:

- Aggregate outcomes by merchant, time window, payment method, issuer family, and failure family.
- Detect a transparent threshold/statistical spike.
- Create a merchant-scoped portfolio incident.
- Defer correlated retries and suppress unnecessary contact.
- Allow unrelated cases to continue.
- Resolve the incident using explicit criteria and gradually re-evaluate affected cases.

### B2B promise-to-pay

Supported MVP behavior:

- Create a case from an overdue invoice.
- Send staged, policy-approved simulated/test outreach.
- Extract structured intent, promised date, and promised amount.
- Schedule a durable reminder.
- Verify payment or detect a broken promise.
- Stop automated contact immediately on a dispute and escalate to a human.

## Deferred capabilities

The following are architecturally supported but not MVP commitments:

- Checkout-abandonment recovery.
- Mandate retry sequencing beyond the core subscription workflow.
- Hinglish voice calls or production communication delivery.
- Live Razorpay credentials or real-money movement.
- Autonomous refunds, amount changes, direct debits, or unrestricted tool use.
- Multi-currency conversion and consolidated cross-currency recovery reporting.
- Complex deep-learning scoring or generative anomaly detection.
- A claim of regulatory certification or absolute audit immutability.

## MVP permissions

Default merchant policy is deny-by-default. A merchant must explicitly allow an action type and channel. The MVP may demonstrate only:

- Test Mode payment-link creation.
- Deferred/scheduled retry recommendation, with provider execution only when supported safely in Test Mode.
- Simulated/test email or messaging adapters.
- Human review and no-op/stop actions.

Refunds, amount modifications, live charges, and live voice calls are prohibited.

## Contract change policy

- Backward-compatible additions remain within major version `1` and require examples/tests.
- Removing/renaming fields, changing meaning, tightening previously valid values, or changing money/state semantics requires a new major contract version.
- Consumers must reject unsupported major versions safely.
- Historical records retain the contract version used when created.

## Phase 0 exit criteria

- This scope is frozen and linked from the contract index.
- All five schemas and examples exist and validate.
- State, decision, action, outcome, money, and version semantics are machine checked.
- Required ADRs are accepted.
- Evaluation success criteria are frozen before implementation results exist.
- No contract gives an LLM or graph direct execution authority.
