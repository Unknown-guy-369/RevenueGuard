# RevenueGuard system architecture

Open [the interactive architecture](revenueguard-architecture.html). Its editable input is [revenueguard.architecture.json](revenueguard.architecture.json).

This is a logical component map of the working tree inspected on 2026-09-05, including uncommitted workflow fixes. It is not a production deployment certification. Repository HEAD at inspection was `3c66da2103ec38d98b1092432c2a903b03f4d175`; the diagram deliberately does not pin source links to that commit because the working tree differs from it.

The main rail follows accepted events into recovery decisions. The lower rail follows authorized execution back to evidence and stored outcomes. Arrows show selected functional relationships; they are not an exhaustive list of database calls. All persistence uses merchant-scoped repositories backed by PostgreSQL. Inbox and action-outbox records are PostgreSQL data, even though their dispatch responsibilities are grouped with workers in the diagram. Redis carries work and is not the financial source of truth.

## Components and source evidence

| Diagram component          | Responsibility and boundary                                                                                                                      | Inspected source                                                                                                                                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Revenue events             | Razorpay Test Mode webhooks, merchant receivables, and explicitly synthetic checkout events                                                      | [Webhook route](../../apps/api/src/revenueguard_api/routes/webhooks.py), [API wiring](../../apps/api/src/revenueguard_api/main.py)                                                                                     |
| FastAPI gateway            | Verify unmodified raw-body signatures, resolve the merchant, persist accepted events, then acknowledge                                           | [Ingestion persistence](../../apps/api/src/revenueguard_api/persistence.py), [webhook contracts](../../apps/api/src/revenueguard_api/webhooks.py)                                                                      |
| Inbox and dispatch         | PostgreSQL inbox/dispatch records feed Celery tasks through Redis; leases, retries, dead-letter retention and replay handle delivery failures    | [Worker tasks](../../apps/worker/src/revenueguard_worker/tasks.py), [queue configuration](../../apps/worker/src/revenueguard_worker/celery_app.py)                                                                     |
| Recovery case engine       | Normalize and correlate provider identity; enforce versioned case transitions; re-evaluate deferred cases                                        | [Recovery service](../../packages/integrations/src/revenueguard_integrations/recovery/service.py), [state machine](../../packages/domain/src/revenueguard_domain/cases.py)                                             |
| Bounded LangGraph          | Load permitted evidence, assist diagnosis, generate strategies, rank and explain; use typed outputs and deterministic fallback                   | [Case graph](../../packages/agents/src/revenueguard_agents/graph.py), [model providers](../../packages/agents/src/revenueguard_agents/providers.py)                                                                    |
| Portfolio and receivables  | Detect degradation incidents, supply policy constraints, track promises and freeze disputed or claimed-paid invoices                             | [Playbook services](../../packages/integrations/src/revenueguard_integrations/playbooks/service.py), [promise maintenance](../../apps/worker/src/revenueguard_worker/playbook_tasks.py)                                |
| Deterministic policy       | Apply versioned allowed actions, economics, confidence, ceilings, consent, quiet hours, incidents, payment state and human approval              | [Policy engine](../../packages/domain/src/revenueguard_domain/policy.py)                                                                                                                                               |
| Action outbox and executor | Atomically record authorized intent, use stable business keys, claim actions, recheck policy and count provider attempts                         | [Action repository](../../packages/integrations/src/revenueguard_integrations/persistence/action_repositories.py), [execution service](../../packages/integrations/src/revenueguard_integrations/execution/service.py) |
| Provider adapters          | Configured Razorpay Test Mode handles payment-link creation; the current worker routes other action types through the deterministic simulator    | [Adapters](../../packages/integrations/src/revenueguard_integrations/execution/providers.py), [provider selection](../../apps/worker/src/revenueguard_worker/tasks.py)                                                 |
| Outcome verification       | Reconcile provider lookups and signed success events; preserve uncertainty; credit eligible actions only from authoritative evidence             | [Verification service](../../packages/integrations/src/revenueguard_integrations/execution/service.py)                                                                                                                 |
| PostgreSQL                 | Store merchant policies, provider entities, inbox, cases, transitions, predictions, reviews, actions, attempts, incidents, promises and outcomes | [SQLAlchemy models](../../packages/integrations/src/revenueguard_integrations/persistence/models.py)                                                                                                                   |
| Next.js dashboard          | Server-side operator session and merchant API proxy; present payments, cases, incidents, approvals, evidence and synthetic sessions              | [Session boundary](../../apps/web/lib/auth/session.ts), [API proxy](../../apps/web/lib/api/proxy.ts), [API routes](../../apps/api/src/revenueguard_api/main.py)                                                        |

## Recovery and failure behavior

The normal case progression is `DETECTED → DIAGNOSING → DECISION_PENDING → POLICY_CHECK → READY → EXECUTING → VERIFYING → RECOVERED`. A verified recovery requires authoritative evidence; provider request acceptance alone leaves the case verifying.

| Policy or failure outcome            | Durable response                                                                           |
| ------------------------------------ | ------------------------------------------------------------------------------------------ |
| `PROCEED`                            | Authorize an action in the outbox; recheck policy immediately before each provider attempt |
| `DEFER`                              | Persist `next_evaluation_at`; scheduled workers re-evaluate policy when due                |
| `SKIP`                               | Omit the candidate; no-action results remain decision pending                              |
| `STOP`                               | Move incompatible automation to the terminal `STOPPED` state                               |
| `REQUIRE_HUMAN`                      | Create an action-bound, expiring review; approval must still satisfy current policy        |
| Duplicate event or work delivery     | Database uniqueness and stable action identity suppress duplicate business effects         |
| Explicit retryable provider failure  | Record the attempt and schedule bounded backoff                                            |
| Timeout or lost worker during a call | Record `UNKNOWN`; block equivalent execution while reconciling                             |
| Verification deadline exhausted      | Preserve uncertainty and escalate for review                                               |
| Customer reports already paid        | Freeze outreach and escalate verification without marking money recovered                  |
| Model unavailable or malformed       | Retain safe deterministic candidates and record fallback evidence                          |

These describe the implemented code paths, not a claim that this diagram task reran every safety test. The prior browser check also identified expired approvals still displayed with enabled controls; diagram generation does not fix that UI defect.

## Implemented foundations versus target capabilities

The three scoped playbooks have implementations: failed subscriptions, payment degradation with deferral, and overdue invoices with promise/dispute handling. Bounded case intelligence, provider simulation, webhook replay, decision receipts and model trace records exist. These foundations do not establish complete production readiness.

The target architecture additionally calls for a complete portfolio decision governor and customer contact coordination across workflows; a SHA-256 tamper-evident audit ledger; full operational telemetry; calibrated recovery scoring; seeded, held-out evaluation against baselines and ablations; authoritative cost/net recovery reporting; real contact adapters; and a production durable-queue mapping. Checkout abandonment, mandate sequencing and Hinglish voice are extension playbooks. The existing synthetic checkout generator does not itself implement abandonment recovery.

The replay harness is implemented in [webhook_replay.py](../../packages/evaluation/src/revenueguard_evaluation/webhook_replay.py). It checks protocol and delivery behavior; it is not the planned statistical recovery benchmark. Model tracing has its own optional adapter in [tracing.py](../../packages/agents/src/revenueguard_agents/tracing.py), which is distinct from a complete audit ledger.

## Runtime and authority boundaries

- Development runs Next.js, FastAPI and a Celery worker with Beat as separate processes. Docker Compose currently starts only PostgreSQL and Redis. Application Dockerfiles exist for separate image builds.
- PostgreSQL holds durable wakeups; Beat periodically dispatches due work. Event and action dispatch run every 5 seconds, reconciliation and deferred-case evaluation every 30 seconds, and promise maintenance every 60 seconds.
- LLM/model endpoints are optional and configured server-side. The case graph receives read-only evidence; it cannot execute a payment or customer contact.
- Razorpay operations remain in Test Mode. Synthetic action evidence must remain labelled and excluded from real merchant recovery totals.
- Money is stored in integer minor units with explicit currency. Merchant identity scopes persistence, API authorization and operator views.

Canonical references: [ARCHITECTURE.md](../../ARCHITECTURE.md), [IMPLEMENTATION_PLAN.md](../../IMPLEMENTATION_PLAN.md), [worker launch targets](../../Makefile), [local infrastructure](../../docker-compose.yml).

## Artifact verification

Regenerate through the installed Archify skill using `validate architecture`, then `deliver architecture`, both with `--quality showcase`. Run `visual-check` on the delivered HTML. The JSON source is static; motion is off by default. The viewer provides themes, focus, search, pan/zoom and export controls.

The [delivery receipt](revenueguard-architecture.receipt.json) records exact input and output hashes and separates artifact validation, automated browser evidence and visual review.
