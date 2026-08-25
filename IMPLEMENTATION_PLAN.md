# RevenueGuard Implementation Plan

This plan turns the architecture in `sources/RevenueGuard — Unified Agentic Revenue Recovery Control Plane.md` into an implementation sequence from repository scaffolding through deployment and evaluation.

## 1. Delivery strategy

Build one complete, safe recovery path before adding breadth. The first vertical slice is:

```text
Razorpay webhook
→ verified and deduplicated event
→ normalized failure
→ recovery case
→ deterministic diagnosis and policy decision
→ outbox action
→ simulated or test-mode execution
→ verified outcome
→ audit receipt and dashboard metric
```

The initial deep use cases are:

1. Failed-subscription recovery.
2. Payment-degradation detection and safe retry deferral.
3. B2B promise-to-pay tracking with dispute escalation.

Checkout abandonment, mandate sequencing, and voice recovery remain extension playbooks until the three core paths meet their exit criteria.

## 2. Target repository structure

```text
RevenueGuard/
├── apps/
│   ├── api/                    # FastAPI application
│   ├── worker/                 # Celery workers and scheduled jobs
│   └── web/                    # Next.js merchant dashboard
├── packages/
│   ├── domain/                 # Case states, commands, policies, money types
│   ├── agents/                 # LangGraph graphs and bounded tools
│   ├── integrations/           # Razorpay, LLM and notification adapters
│   ├── observability/          # Logging, metrics and tracing helpers
│   └── evaluation/             # Generator, simulator, baselines and reports
├── migrations/                 # Alembic migrations
├── fixtures/
│   ├── razorpay/               # Sanitized real test-mode payloads
│   └── synthetic/              # Generated evaluation manifests
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── end_to_end/
│   ├── resilience/
│   └── evaluation/
├── deploy/
│   ├── docker/
│   └── environments/
├── scripts/                    # Developer and CI entry points
├── sources/                    # Architecture and research inputs
├── .github/workflows/
├── docker-compose.yml
├── Makefile
└── README.md
```

Keep the Python domain package independent of FastAPI, Celery, LangGraph, and external SDKs. This makes financial rules testable without infrastructure or model calls.

## 3. Phase overview

| Phase | Outcome | Depends on |
|---|---|---|
| 0 | Scope, contracts and success criteria frozen | — |
| 1 | Reproducible monorepo and local environment | 0 |
| 2 | Transactional event ingestion and core database | 1 |
| 3 | Recovery case state machine and deterministic policies | 2 |
| 4 | Safe action outbox, executor and outcome verification | 3 |
| 5 | Bounded LangGraph case intelligence | 3–4 |
| 6 | Three complete recovery playbooks | 5 |
| 7 | Portfolio intelligence and cross-case coordination | 6 |
| 8 | Auditability, observability and merchant dashboard | 4–7 |
| 9 | Full test and resilience program | Continuous; closes after 8 |
| 10 | Reproducible offline and test-mode evaluation | 6–9 |
| 11 | Security, performance and release hardening | 9–10 |
| 12 | Staging, production-like deployment and demo release | 11 |

## 4. Phase 0 — Freeze the product contract

### Work

- Define the MVP actors: merchant operator, recovery worker, human approver and customer contact adapter.
- Define the canonical `RevenueRiskEvent`, `RecoveryCase`, `DecisionReceipt`, `RecoveryAction` and `VerifiedOutcome` schemas.
- Define money as integer minor units plus ISO currency; never use floating-point values.
- Define the allowed case transitions and terminal states.
- Define decision outcomes: `PROCEED`, `DEFER`, `SKIP`, `STOP`, and `REQUIRE_HUMAN`.
- Define action outcome states: `PENDING`, `SUCCEEDED`, `FAILED`, and `UNKNOWN`.
- Freeze the first three playbooks and explicitly list deferred features.
- Record evaluation success criteria before generating results.

### Exit gate

- Architecture decision records exist for source of truth, queues, agent boundaries, idempotency, outcome verification and synthetic-data disclosure.
- Schemas have examples and version fields.
- No LLM or workflow framework has authority to execute a financial action directly.

## 5. Phase 1 — Scaffold the repository

### Work

- Initialize Python and Node workspaces with pinned versions.
- Create FastAPI, worker and Next.js applications.
- Add PostgreSQL, Redis and local object/storage dependencies to Docker Compose.
- Add SQLAlchemy, Alembic, Pydantic, Celery, LangGraph and the Razorpay SDK.
- Add Ruff, mypy, pytest, ESLint, TypeScript checking and frontend tests.
- Add `.env.example`; keep all real keys out of the repository.
- Add Make targets or equivalent commands for setup, development, migration, tests, linting and evaluation.
- Add GitHub Actions for formatting, type checks, unit tests, integration tests and builds.
- Add health, readiness and version endpoints.

### Exit gate

- A new developer can start PostgreSQL and Redis, migrate the database, run the API, worker and web app, and execute the test suite using documented commands.
- CI passes from a clean checkout.
- No secret is present in tracked files or container images.

## 6. Phase 2 — Build event ingestion and persistence

### Work

- Implement a raw-body Razorpay webhook endpoint.
- Verify signatures before parsing or dispatching business logic.
- Store every accepted webhook in `webhook_events` with a unique provider event ID.
- Return `2xx` after durable inbox storage, not after full recovery processing.
- Record invalid signatures without placing their payloads on the processing queue.
- Normalize supported Razorpay events into versioned internal events.
- Implement a transactional inbox-to-queue dispatcher with retry and dead-letter handling.
- Add merchant, customer, payment, subscription and event-correlation tables.
- Build a replay CLI for normal, duplicate, invalid-signature, delayed, burst and out-of-order events.

### Exit gate

- Replaying the same provider event five times creates one processed event and no more than one business effect.
- Invalid signatures never reach normalization.
- Raw fixtures and normalized snapshots pass contract tests.
- An API or worker crash after inbox commit does not lose the event.

## 7. Phase 3 — Implement the recovery domain and policy engine

### Work

- Implement the recovery case finite-state machine in the domain package.
- Enforce valid transitions in both application code and database constraints where practical.
- Persist every transition with actor, reason, correlation ID and policy version.
- Implement deterministic diagnosis rules for the initial known failure families.
- Implement guardians for retry limits, contact limits, consent, quiet hours, disputes, already-paid cases, minimum expected value, high-value approval and active portfolio incidents.
- Make policy evaluation pure and deterministic: the same input and policy version must return the same result.
- Store immutable policy versions and attach the evaluated version to every decision.
- Add human-review requests and resume semantics.

### Exit gate

- Unit tests cover every state transition, guardian and terminal condition.
- Property tests prove retry/contact counters never exceed configured ceilings.
- A dispute, opt-out or confirmed payment immediately prevents additional automated contact.
- Policy changes affect future evaluations without rewriting past receipts.

## 8. Phase 4 — Build exactly-once business effects

### Work

- Implement `recovery_actions`, `action_attempts` and a transactional action outbox.
- Require a stable idempotency key for every externally visible action.
- Separate planning, authorization, execution and verification.
- Build adapters for Razorpay Test Mode and a deterministic local simulator.
- Add bounded retries with exponential backoff and dead-letter routing.
- Treat transport timeouts and ambiguous provider responses as `UNKNOWN`.
- Block another equivalent action while the previous result is unknown.
- Reconcile unknown outcomes using provider lookup or subsequent signed webhook events.
- Make provider webhooks, not API acknowledgements, authoritative for recovered-money accounting when applicable.

### Exit gate

- A worker crash before, during or after an external call does not create duplicate business effects.
- Timeout tests enter `UNKNOWN`, suppress duplicates and later reconcile correctly.
- Dashboard recovery totals include only verified outcomes.

## 9. Phase 5 — Add bounded agent intelligence

### Work

- Implement a typed LangGraph state for case context, evidence, predictions, candidate strategies and proposed decisions.
- Create bounded nodes for context retrieval, diagnosis assistance, strategy generation, ranking and explanation.
- Require schema-validated structured output from every model node.
- Allow only read-only tools inside reasoning nodes.
- Place the deterministic policy engine after agent recommendations and before the action outbox.
- Add timeouts, token limits, retry limits and deterministic fallbacks.
- Persist model, prompt, schema and feature versions with each prediction and receipt.
- Redact secrets and unnecessary personal data from model inputs and traces.

### Exit gate

- Malformed, unavailable or slow LLM responses fall back safely without blocking deterministic workflows.
- The agent cannot call Razorpay or a communication provider directly.
- Every recommendation is traceable to evidence and remains reproducible at the policy-decision boundary.

## 10. Phase 6 — Complete the three core playbooks

### 6.1 Failed subscription recovery

- Handle pending, charged and halted subscription events.
- Diagnose insufficient funds, expired method, authentication failure and temporary infrastructure failure.
- Support deferred retry, payment-method update link, payment link, reminder, stop and escalation.

### 6.2 B2B promise-to-pay

- Ingest overdue invoice events and customer responses.
- Extract promise date, amount and intent using bounded structured extraction.
- Schedule reminders deterministically.
- Freeze automation on disputes and escalate to a human.

### 6.3 Payment degradation

- Aggregate recent outcomes by merchant, method, issuer family, error family and time window.
- Detect spikes using a transparent statistical baseline first.
- Create portfolio incidents that pause unsafe retries and suppress unnecessary customer contact.
- Resume deferred cases only after incident-clear criteria and policy re-evaluation.

### Exit gate

- Each playbook has a documented happy path, failure path, stop path, human-review path and verified-outcome path.
- End-to-end tests exercise each path without manual database edits.
- Deferred workflows survive worker and API restarts.

## 11. Phase 7 — Portfolio intelligence and coordination

### Work

- Add scheduled and streaming portfolio aggregations.
- Implement incident creation, update and resolution rules.
- Add cross-case customer identity resolution.
- Add a contact governor that prevents multiple playbooks from contacting the same customer independently.
- Rank allowed actions using expected net recovery:

```text
P(recovery) × amount
− action cost
− risk penalty
− customer-friction penalty
```

- Start with logistic regression or another interpretable calibrated model; introduce boosted trees only if held-out results justify the added complexity.
- Preserve the deterministic policy result as a hard constraint over optimization.

### Exit gate

- A simulated systemic spike defers the correlated cases while unrelated failures continue normally.
- Multiple cases for one customer produce one coordinated intervention.
- Incident resolution safely resumes eligible cases without causing a retry storm.

## 12. Phase 8 — Auditability, observability and dashboard

### Work

- Implement decision receipts containing evidence, candidates, scores, selected action, policy result, versions and outcome.
- Add an append-only audit table with a SHA-256 hash chain and verification command.
- Clearly describe the ledger as tamper-evident, not absolutely immutable.
- Add OpenTelemetry traces and structured logs with correlation IDs.
- Export infrastructure, model and financial-workflow metrics.
- Build dashboard views for revenue at risk, verified gross/net recovery, active cases, incidents, unknown outcomes, policy blocks and escalations.
- Add a case timeline and human-approval queue.
- Never display synthetic results as production merchant outcomes.

### Exit gate

- Every dashboard number links back to stored cases and verified outcomes.
- A ledger verification test detects modified historical entries.
- Operators can identify a failed workflow using logs, traces and its decision receipt.

## 13. Phase 9 — Testing and resilience program

Testing runs throughout implementation; this phase closes the remaining coverage and failure-injection gaps.

### Test layers

| Layer | Purpose |
|---|---|
| Unit | Domain transitions, guardians, scoring and calculations |
| Property | Idempotency, bounded retries, money invariants and state-machine safety |
| Contract | Razorpay payloads, model schemas and provider adapters |
| Integration | PostgreSQL transactions, Redis/Celery delivery, migrations and outbox |
| End-to-end | Webhook-to-verified-recovery workflows |
| Resilience | Crashes, timeouts, duplicates, reordering, queue pressure and provider failure |
| Security | Signature bypass, replay, authorization, secret leakage and tenant isolation |
| Load | Burst ingestion, worker throughput, queue recovery and database contention |

### Mandatory failure scenarios

- Duplicate webhook delivery.
- Out-of-order success and failure events.
- Invalid or missing signature.
- Razorpay timeout after a request may have succeeded.
- Worker crash at every action boundary.
- LLM timeout, malformed JSON and hallucinated action.
- Duplicate recovery action request.
- Customer already paid, opted out or disputed.
- Same customer with multiple active cases.
- Policy version changes while a workflow is sleeping.
- Systemic failure spike and incident recovery.
- Queue overload and delayed processing.
- Cross-merchant access attempt.

### Release test gates

- Zero policy violations in deterministic test scenarios.
- Zero duplicate external business effects in crash and replay tests.
- Zero unverified amounts counted as recovered.
- All schema migrations upgrade successfully from a clean database and the previous release.
- Critical-path domain and policy code has branch-focused coverage; coverage percentage alone is not treated as proof.

## 14. Phase 10 — Evaluation framework

### 14.1 Data tiers

1. Sanitized Razorpay Test Mode fixtures for integration correctness.
2. A replay harness for delivery and ordering behavior.
3. A seeded synthetic portfolio generator for statistical evaluation.

Every generated record must include `source = SYNTHETIC`, generator version and seed. Hidden outcome probabilities are available only to the simulator, not the agent.

### 14.2 Dataset protocol

- Generate a representative portfolio plus named stress scenarios.
- Split 70% training, 15% validation and 15% held-out test by customer and time to limit leakage.
- Freeze the held-out manifest before strategy tuning.
- Create an out-of-distribution incident set with unseen degradation patterns.
- Run each stochastic simulation across multiple fixed seeds and report mean plus uncertainty intervals.

### 14.3 Baselines

Compare RevenueGuard with:

- No recovery action.
- Immediate static retry.
- Fixed-delay retry.
- Rules-only playbook without ML/LLM ranking.
- Case-only intelligence without portfolio coordination.

This ablation identifies whether the agent, scoring model and portfolio layer each add measurable value.

### 14.4 Model metrics

- Precision, recall and F1.
- ROC-AUC and PR-AUC.
- Brier score and calibration curve.
- Performance by failure category, amount band, payment method and merchant segment.
- Out-of-distribution degradation.

### 14.5 Business and safety metrics

- Revenue at risk.
- Verified gross and net revenue recovered.
- Recovery rate and cost per recovered rupee.
- Incremental net recovery versus each baseline.
- Customer contacts and unnecessary interventions.
- Human escalations and policy blocks.
- Duplicate executions and policy violations.
- Unknown-outcome count, age and reconciliation rate.
- Incident detection precision, recall and time to detect.
- p50/p95 time from event receipt to safe decision.

### 14.6 Evaluation acceptance gate

- RevenueGuard improves net recovered revenue over the best static baseline on the frozen held-out set.
- The improvement is reported with raw counts and uncertainty, not only a percentage.
- Policy violations and duplicate effects remain zero in controlled evaluation.
- Calibration and subgroup results are disclosed, including regressions.
- Synthetic outcomes are explicitly labelled and never presented as real merchant recovery rates.
- The evaluation command produces a versioned machine-readable result plus a human-readable report from a clean checkout.

## 15. Phase 11 — Security and production hardening

### Work

- Add authentication, merchant-scoped authorization and tenant isolation tests.
- Encrypt traffic and sensitive data; minimize retained customer data.
- Move Razorpay, webhook and LLM secrets to the deployment platform's secret manager.
- Add rate limits, payload-size limits, request timeouts and abuse controls.
- Define retention and redaction policies for webhooks, model traces and communications.
- Pin and scan dependencies and container images.
- Add database backups, restore drills and migration rollback/runbook procedures.
- Load-test normal traffic, burst webhooks, delayed queue recovery and incident resumption.
- Establish service-level objectives for ingestion availability, processing latency and unknown-outcome reconciliation.

### Exit gate

- Threat review has no unresolved critical finding.
- Backup restoration and secret rotation are demonstrated in staging.
- Load tests meet the documented SLOs with bounded queue growth.
- Operational runbooks cover webhook failure, queue backlog, database degradation, provider outage and model outage.

## 16. Phase 12 — Deployment and release

### Environments

```text
local
→ CI ephemeral integration environment
→ staging with Razorpay Test Mode
→ production-like demo environment
```

Do not connect real payment credentials or real customer communication channels for the hackathon release.

### Recommended prototype deployment

- Next.js dashboard: Vercel or equivalent.
- FastAPI and Celery workers: Render, Railway or equivalent container platform.
- Managed PostgreSQL and Redis.
- Object storage for evaluation artifacts if needed.
- Hosted OpenTelemetry backend and optional LangSmith traces with redaction.

### Release steps

1. Build immutable API, worker and web artifacts in CI.
2. Run static analysis, unit, integration, contract and migration tests.
3. Deploy database migrations using a one-shot release job.
4. Deploy API and workers with health/readiness checks.
5. Deploy the web application against the staging API.
6. Configure secrets and the Razorpay Test Mode webhook endpoint.
7. Run smoke tests and a signed webhook canary.
8. Run duplicate, timeout and unknown-outcome canaries.
9. Execute the frozen evaluation suite and attach its report to the release.
10. Tag the release and record schema, policy, model, prompt, generator and application versions.

### Deployment exit gate

- The public demo shows a real Razorpay Test Mode event flowing through ingestion, decisioning, policy, execution/verification and audit.
- The demo can replay a duplicate event and prove one business effect.
- The demo can simulate a provider timeout and show `UNKNOWN` followed by safe reconciliation.
- The batch evaluation is reproducible and linked from the dashboard or release notes.
- Rollback of the application version is tested without rolling back financial history.

## 17. Demo and judging sequence

The final demonstration should tell one coherent story:

1. Show the merchant policy and bounded permissions.
2. Send a genuine Razorpay Test Mode failure webhook.
3. Open the resulting case and its evidence-backed recommendation.
4. Show the deterministic policy decision and action idempotency key.
5. Verify the outcome before counting recovered money.
6. Replay the webhook several times and show one business effect.
7. Simulate an ambiguous timeout and show the safe `UNKNOWN` state.
8. Inject a 60-of-100 systemic spike and show coordinated deferral.
9. Show the held-out batch comparison against static baselines.
10. Open the complete decision receipt and tamper-evident audit check.

## 18. Definition of done

RevenueGuard is ready for submission when:

- All three core playbooks work end to end.
- Financial actions pass deterministic policy gates and use stable idempotency keys.
- Recovered money is based only on verified outcomes.
- Duplicates, out-of-order events, crashes and ambiguous timeouts are handled safely.
- Portfolio incidents coordinate case-level behavior.
- Human review, stopping rules and customer-contact limits are enforced in code.
- Audit receipts and operational telemetry explain every significant decision.
- CI, staging deployment, smoke tests and rollback procedures work from documented commands.
- A frozen, seeded evaluation shows honest model, business and safety metrics against credible baselines.
- All synthetic claims are clearly disclosed as simulated results.

## 19. Suggested milestone order

If time is constrained, preserve this order:

```text
M1  Scaffold + database + webhook inbox
M2  Case state machine + policy engine
M3  Outbox + executor + UNKNOWN reconciliation
M4  Failed-subscription vertical slice
M5  Replay/resilience harness + audit receipt
M6  Portfolio degradation controller
M7  B2B promise-to-pay workflow
M8  Dashboard + observability
M9  Frozen evaluation + baseline comparison
M10 Staging deployment + demo hardening
```

Do not trade away idempotency, policy enforcement, verified outcomes or reproducible evaluation to add more playbooks. Those four properties are the core proof that RevenueGuard is safe, production-minded and measurable.
