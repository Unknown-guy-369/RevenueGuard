# Phase 6 core playbooks

Phase 6 completes the failed-subscription, B2B promise-to-pay, and payment-degradation
workflows without creating a second execution path. Every external action still passes the
versioned deterministic policy engine, enters the PostgreSQL outbox with a stable idempotency
key, and requires authoritative evidence before recovered money is counted.

## Failed-subscription recovery

`subscription.pending` and `subscription.halted` are recovery failures, while
`subscription.charged` is authoritative success evidence. A halted subscription is not treated
as merchant cancellation.

| Path | Durable behavior |
| --- | --- |
| Happy | Diagnose the payment failure, authorize retry/method update/payment link/reminder through policy, execute through the outbox, and accept `subscription.charged` or a provider lookup as verification. |
| Failure | Explicit provider rejection returns to decisioning when attempts remain; ambiguous calls enter `UNKNOWN` and suppress equivalent actions. |
| Stop | Confirmed cancellation, already-paid evidence, dispute, opt-out, or a retry/contact ceiling blocks incompatible automation. |
| Human review | High-value, low-confidence, or unknown failures enter the existing human-review path and are rechecked before execution. |
| Verified outcome | Only signed provider evidence or an authoritative provider lookup transitions the case to `RECOVERED` and updates totals. |

## B2B promise-to-pay

Authenticated merchant invoice events are stored separately from Razorpay webhooks and normalized
into the common recovery-event contract. Customer reply text is processed transiently: PostgreSQL
retains a SHA-256 digest and the bounded structured extraction, not the raw message.

```text
invoice.overdue
→ recovery case and policy-approved reminder
→ bounded intent/date/amount extraction
→ durable promise and reminder time
→ policy/outbox reminder authorization
→ authoritative payment verification or broken-promise escalation
```

| Path | Durable behavior |
| --- | --- |
| Happy | A valid promise preserves invoice currency, cannot exceed the outstanding minor-unit amount, and survives API/worker restarts in `promises_to_pay`. |
| Failure | Malformed, slow, oversized, or unavailable extraction returns typed `UNKNOWN`; no terms are invented. |
| Stop | A dispute sets the invoice to `DISPUTED`, freezes automation, and causes the mandatory pre-execution policy check to cancel stale outreach. |
| Human review | Disputes, broken promises, already-paid claims awaiting verification, and unknown/help intents create tenant-scoped receivable escalations. |
| Verified outcome | An API/contact acknowledgement counts zero revenue; an authoritative provider/merchant-ledger lookup is required to recover the case. |

The Celery beat task `revenueguard.playbooks.maintain_promises` claims due promises with
`FOR UPDATE SKIP LOCKED`. Quiet hours move the durable reminder time. A reminder is recorded as
scheduled only after its decision receipt and outbox action commit atomically. Broken promises
move the invoice to `ESCALATED`, freeze later automation, and open a human escalation.

## Payment degradation

Payment observations are merchant-scoped and keyed by source event ID. Detection groups outcomes
by payment method, issuer family, error family, and time window. All calculations use integer
basis points.

The default transparent rule requires:

- at least 20 observations in the preceding 24-hour baseline (excluding the current window);
- at least 10 observations in the current 15-minute window;
- current failure rate of at least 25%;
- failure-rate increase of at least 15 percentage points; and
- current failure rate at least 2× baseline.

| Path | Durable behavior |
| --- | --- |
| Happy | Normal dimensions remain unaffected and cases continue independently. |
| Failure | A qualifying spike creates or refreshes one active incident per tenant/dimension with raw counts, rates, threshold version, and evidence. |
| Stop/defer | Matching retry and money-intent candidates receive `DEFER / ACTIVE_INCIDENT`; unrelated dimensions and merchants are isolated. |
| Human review | Broken incident assumptions remain inspectable through evidence and can be escalated without fabricating provider state. |
| Resolution | Two consecutive clear windows resolve the incident and assign affected deferred cases deterministic 30-second staggered resume times. Each case re-enters the ordinary policy path. |

## Verification map

- Pure contracts, extraction bounds, money preservation, and baseline math:
  `tests/unit/test_phase6_playbooks.py`.
- Halted-subscription behavior, receivables happy/stop/human/verified paths, durable restarts,
  incident isolation, and gradual resume: `tests/integration/test_phase6_playbooks.py`.
- Clean and incremental schema upgrades: `tests/integration/test_phase3_migrations.py`.
- Worker registration and durable maintenance schedule:
  `apps/worker/tests/test_playbook_tasks.py`.
