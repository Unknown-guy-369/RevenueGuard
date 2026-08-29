# AGENTS.md

This file defines the repository-wide working rules for every coding agent and contributor working on RevenueGuard. It applies to the repository root and every descendant directory unless a more specific `AGENTS.md` adds stricter local instructions.

## 1. Project objective

RevenueGuard is a unified, event-driven, agentic revenue recovery control plane for Razorpay merchants.

The system must:

- Detect revenue at risk across failed payments, failed subscriptions, payment degradation, checkout abandonment, mandates, and overdue receivables.
- Convert provider-specific events into durable, standardized recovery cases.
- Diagnose the cause and recommend the safest economically useful recovery strategy.
- Coordinate decisions at both individual-case and merchant-portfolio levels.
- Apply deterministic merchant policy before every money-related or customer-contact action.
- Execute approved actions idempotently through ordinary services, never directly through an LLM.
- Verify outcomes using authoritative provider state before counting recovered money.
- Stop, defer, or escalate safely when confidence, consent, policy, provider state, or execution outcome is uncertain.
- Produce a complete, explainable, tamper-evident audit history.
- Evaluate verified gross and net money recovered across reproducible batches against credible baselines.

The project is successful only if it is demonstrably safe, testable, measurable, and resilient. A polished dashboard without correct financial workflow behavior is not sufficient.

## 2. Source documents and precedence

Read the relevant parts of these documents before making architectural or cross-cutting changes:

1. `AGENTS.md` — repository operating constraints.
2. The current user request — task-specific authority and acceptance criteria.
3. `ARCHITECTURE.md` — canonical implementation-facing system architecture and invariants.
4. `IMPLEMENTATION_PLAN.md` — implementation phases, test gates, evaluation, and deployment sequence.
5. `sources/RevenueGuard — Unified Agentic Revenue Recovery Control Plane.md` — detailed design rationale and original source material.

Do not silently contradict these documents. If a requested change conflicts with a safety invariant or requires an architectural deviation, explain the conflict and update the relevant architecture decision/document as part of the change.

### 2.1 Codebase exploration and navigation

When exploring, navigating, or seeking to understand the codebase architecture, file relationships, or component structure, agents MUST use `graphify` (the knowledge graph / graphify skill, especially when `graphify-out/` exists) to query and inspect codebase context quickly instead of reading every individual file line-by-line.

## 3. Product scope

Implement the core workflows deeply in this order:

1. Failed-subscription recovery.
2. Payment-degradation detection and safe retry deferral.
3. B2B receivables and promise-to-pay tracking with dispute escalation.

Checkout abandonment, mandate retry sequencing, and Hinglish voice recovery are extension playbooks. Do not weaken idempotency, policy enforcement, verified outcomes, resilience, or evaluation to add more playbooks.

The hackathon/demo environment must use Razorpay Test Mode. Do not use live payment credentials or real customer-contact channels unless the user explicitly expands the scope and the system has completed an appropriate production/security review.

## 4. Architecture

The canonical flow is:

```text
Razorpay / merchant event
→ raw webhook gateway
→ signature verification
→ idempotent event inbox
→ durable queue
→ normalization and correlation
→ recovery case state machine
→ case reasoning + portfolio intelligence
→ decision governor
→ deterministic policy engine
→ action outbox
→ action executor
→ provider
→ outcome verification / reconciliation
→ case transition, audit receipt, and verified metrics
```

### 4.1 Major component boundaries

- **Webhook gateway:** authenticate, validate, persist, acknowledge quickly.
- **Event inbox:** durable deduplication and replay source.
- **Queue:** at-least-once asynchronous delivery, retry, delay, and dead-letter handling.
- **Normalizer:** convert external payloads into versioned internal events.
- **Recovery case engine:** own the durable state machine and transitions.
- **LangGraph case graph:** perform bounded, typed reasoning over read-only evidence.
- **Portfolio intelligence:** detect systemic incidents and cross-case/customer conflicts.
- **Decision governor:** rank candidate actions by expected net recovery under portfolio constraints.
- **Policy engine:** deterministically authorize, defer, skip, stop, or require human review.
- **Action outbox:** commit authorized intent atomically with case state.
- **Executor:** perform approved external actions using stable idempotency keys.
- **Outcome verifier:** reconcile provider truth and preserve explicit uncertainty.
- **Audit and observability:** explain and trace every material decision and effect.
- **Dashboard:** display derived operational state; never become the source of truth.

### 4.2 Source of truth

PostgreSQL is the authoritative store for:

- Merchant configuration and versioned policies.
- Webhook inbox events and processing state.
- Customers, payments, subscriptions, invoices, and correlations.
- Recovery cases and transitions.
- Portfolio incidents.
- Predictions and decision receipts.
- Actions, attempts, human reviews, promises to pay, and verified outcomes.
- Financial metrics and audit entries.

Redis may be used for queues, caching, rate limiting, delayed work, and distributed coordination. Redis, Celery state, LangGraph memory, model memory, browser state, and frontend state are never authoritative financial stores.

## 5. Technology stack

Use the toolchain versions pinned by repository configuration once they exist. Do not introduce a second package manager or competing framework without explicit justification.

| Layer | Technology |
|---|---|
| Frontend | Next.js + TypeScript |
| Backend API | FastAPI + Python |
| Validation and API schemas | Pydantic |
| Domain persistence | SQLAlchemy ORM/Core |
| Database | PostgreSQL |
| Migrations | Alembic |
| Agent orchestration | LangGraph |
| LLM utilities | LangChain only where useful |
| Initial ML | Calibrated logistic regression |
| Optional advanced ML | LightGBM or XGBoost only if evaluation justifies it |
| Prototype queue | Celery + Redis |
| Production queue mapping | SQS-style durable queue |
| Payments | Razorpay Test Mode |
| Observability | OpenTelemetry + structured logs; optional redacted LangSmith traces |
| Containerization | Docker |
| CI/CD | GitHub Actions |
| Audit | PostgreSQL append-only records + SHA-256 hash chain |

### 5.1 ORM rules

- Use SQLAlchemy ORM for ordinary domain persistence and relationships.
- Use SQLAlchemy Core or explicit parameterized SQL for locking, outbox claiming, bulk aggregation, and performance-critical queries when it is clearer or safer.
- Never interpolate user/provider data into SQL strings.
- Do not expose ORM entities as API contracts. Map them to Pydantic request/response models.
- Avoid accidental lazy loading in API serialization and worker loops.
- Keep transaction boundaries visible in application services.
- Use Alembic for every schema change. Do not rely on runtime `create_all` outside isolated tests.
- Preserve financial constraints in PostgreSQL even when equivalent validation exists in Python.

## 6. Non-negotiable system invariants

Every implementation and review must protect these invariants:

1. PostgreSQL remains the authoritative financial and workflow store.
2. No LLM, LangGraph node, or agent tool can directly perform a money action or customer-contact action.
3. Every external action passes the current deterministic merchant policy.
4. Every external action has a stable idempotency key and durable outbox record.
5. Webhook and queue delivery are treated as at least once.
6. Duplicate events cannot create duplicate business effects.
7. Out-of-order events cannot regress newer authoritative state or reopen a terminal case incorrectly.
8. A provider timeout or ambiguous response becomes `UNKNOWN`; it is never guessed as success or failure.
9. An equivalent action remains blocked while the previous outcome is `UNKNOWN`.
10. Recovered revenue is counted only after authoritative verification.
11. Deferred and human-approved workflows re-evaluate policy immediately before execution.
12. Retry and customer-contact ceilings are enforced in code and cannot be overridden by a prompt.
13. Opt-out, dispute, already-paid, cancelled, and other terminal safety conditions stop incompatible automation.
14. Active portfolio incidents can constrain or override case-level recommendations.
15. Cross-workflow coordination prevents multiple independent contacts to the same customer.
16. Every material recommendation, policy result, action, attempt, outcome, and state transition is traceable.
17. Historical decisions retain their policy, model, prompt, schema, feature, and application versions.
18. Synthetic data and results are always labelled as synthetic.
19. Secrets and unnecessary personal data never enter source control, logs, prompts, traces, or audit payloads.
20. Application rollback never rewrites or rolls back financial history.

Do not weaken an invariant to make a demo pass. Fix the underlying design or report the blocker.

## 7. Financial and data constraints

### 7.1 Money

- Represent money as integer minor units plus an ISO currency code.
- Never use binary floating point for payment amounts, recovered revenue, costs, or financial comparisons.
- Keep currency explicit; never aggregate unlike currencies without an explicit conversion policy and recorded rate.
- Enforce non-negative and valid-range constraints in the database.
- Make gross recovery, cost, and net recovery separate fields and calculations.

### 7.2 Time

- Store timestamps as timezone-aware UTC.
- Preserve provider event time and system receive/process times separately.
- Apply merchant/customer local time only at policy and presentation boundaries.
- Make scheduled wakeups durable and safe across process restarts.

### 7.3 Identity and tenancy

- Scope every financial and workflow record to a merchant.
- Enforce tenant ownership in queries, foreign keys/index strategy, authorization, tests, and telemetry.
- Do not trust customer, merchant, payment, or event IDs supplied by a client without resolving them inside the authenticated merchant scope.
- Preserve source/provider identifiers separately from internal identifiers.

### 7.4 Database correctness

- Use database uniqueness for webhook deduplication and action idempotency.
- Use foreign keys and check constraints for domain invariants where practical.
- Use optimistic concurrency through a state/version column or explicit row locks for state-changing commands.
- Atomically commit related case transitions, decision receipts, and outbox actions.
- Make migration scripts reviewable, forward-safe, and tested from both a clean database and the previous schema.
- Do not execute destructive migrations without an explicit backup, rollout, and recovery plan.

## 8. Event processing constraints

- Verify the webhook signature over the unmodified raw body before parsing it into business objects.
- Use constant-time signature comparison through a well-tested library/helper.
- Return `2xx` only after the valid event is durably stored or recognized as a duplicate.
- Never perform the full recovery workflow inside the webhook request.
- Preserve unsupported or failed events for investigation and controlled replay.
- Require stable correlation and causation IDs through API, queue, worker, model, and provider boundaries.
- Assume duplicates, delay, reordering, bursts, partial failure, and process crashes.
- Use dead-letter handling with observable replay tooling; never silently discard poison events.
- Apply backpressure, per-merchant fairness, and rate limits during overload.

## 9. Recovery state machine constraints

The expected states are:

```text
DETECTED
DIAGNOSING
DECISION_PENDING
POLICY_CHECK
READY
EXECUTING
VERIFYING
UNKNOWN
DEFERRED
ESCALATED
RECOVERED
STOPPED
```

- Centralize transition rules in the domain layer.
- Do not assign state strings ad hoc from route handlers, model nodes, or UI code.
- Persist actor, reason, evidence/correlation, and previous/new versions for each transition.
- Require authoritative evidence for `RECOVERED`.
- Treat `RECOVERED` and `STOPPED` as terminal unless an explicitly modelled corrective event exists.
- Reject stale worker transitions through optimistic concurrency or locking.
- Test every allowed transition and every prohibited transition.

## 10. Agent, LLM, and ML constraints

### 10.1 Allowed AI responsibilities

- Interpret ambiguous permitted context.
- Extract structured intent, promise date, and amount from customer replies.
- Assist diagnosis.
- Generate and rank candidate strategies.
- Draft communication after policy determines the channel/action is permissible.
- Explain a recommendation and summarize evidence.

### 10.2 Forbidden AI responsibilities

- Decide or alter payment amount or currency.
- Bypass policy, consent, approval, retry, contact, dispute, or quiet-hour restrictions.
- Directly call Razorpay or a contact provider.
- Mark an action or case recovered.
- Invent provider state, customer consent, history, policy, or evidence.
- Mutate financial records through unrestricted tools.

### 10.3 Model-call requirements

- Use typed, schema-validated input and output.
- Enforce timeouts, token limits, retry limits, and bounded graph steps.
- Treat malformed output as a failure and use a safe deterministic fallback.
- Store model, prompt, schema, feature, and relevant configuration versions.
- Minimize and redact personal/sensitive data.
- Never place secrets in prompts.
- Make model unavailability degrade gracefully without breaking deterministic safety paths.
- Evaluate calibration and subgroup behavior, not only aggregate accuracy.

## 11. Decision and policy constraints

The policy engine returns exactly one of:

```text
PROCEED
DEFER
SKIP
STOP
REQUIRE_HUMAN
```

- Policy evaluation must be deterministic for the same immutable input and policy version.
- Store reason codes and evidence for every policy result.
- Rank only policy-compatible candidate types, but still run final policy immediately before execution.
- A scoring model may prioritize allowed actions; it cannot convert a forbidden action into an allowed one.
- Expected net recovery may include recovery probability, amount, intervention cost, risk, and customer friction.
- High-value, low-confidence, disputed, or otherwise sensitive actions require human review according to merchant policy.
- Human approval is not permanent authorization; revalidate policy and current provider/customer state before execution.

## 12. Action execution and outcome constraints

- Separate recommendation, authorization, execution, and verification into distinct records and services.
- Create the case transition and action outbox record in one PostgreSQL transaction.
- Build stable idempotency keys from logical business identity, not worker attempt ID.
- Record every provider attempt with request identity, timestamps, response category, and correlation data.
- Bound retries and use backoff.
- Never blindly retry a call that may have succeeded.
- Reconcile uncertain outcomes through provider lookup or later signed events.
- Age and monitor unknown outcomes; escalate when the reconciliation deadline is exceeded.
- Count money recovered only after authoritative confirmation.
- Do not implement fake-success, random-success, or success-on-provider-error fallbacks outside explicitly labelled simulation adapters.

## 13. Portfolio intelligence constraints

- Keep case-level and portfolio-level decisions independently inspectable.
- Start incident detection with transparent statistical rules/baselines before adding complex models.
- Version incident thresholds and feature definitions.
- Record incident evidence, affected population, blast radius, start/resolve reasons, and policy effects.
- Allow unrelated cases to continue while correlated cases are deferred.
- Resume deferred cases gradually after incident resolution and re-evaluate each case.
- Prevent one merchant's incident from affecting another merchant.
- Coordinate active cases for the same customer to avoid duplicate or conflicting outreach.

## 14. Security and privacy constraints

- Keep Razorpay, webhook, database, LLM, and contact-provider secrets in environment/secret management, never tracked files.
- Authenticate operators and enforce merchant-scoped authorization for every read and mutation.
- Encrypt traffic and sensitive data as appropriate.
- Apply payload-size limits, request timeouts, rate limits, and replay controls.
- Sanitize logs and error messages.
- Never log full secrets, authorization headers, raw card data, or unnecessary personal information.
- Define retention and deletion behavior for raw webhooks, messages, model traces, and audit data.
- Use least-privilege database and provider credentials.
- Do not expose internal stack traces or provider secrets through API responses.
- Add tenant-isolation and authorization regression tests for every new resource type.

## 15. Audit and observability constraints

- Every material event and action must be traceable through a correlation ID.
- Decision receipts must contain evidence, candidates, scores, selected recommendation, policy result, versions, and outcome reference.
- Audit entries are append-only at the application level and linked with a SHA-256 hash chain.
- Describe the ledger as tamper-evident, not absolutely immutable.
- Never update historical audit entries to make results look correct.
- Export infrastructure, agent, and financial-workflow metrics separately.
- Dashboard totals must be derived from authoritative stored records.
- Provide enough telemetry to diagnose queue backlog, stale unknown outcomes, policy blocks, duplicate prevention, and incident behavior.

## 16. Evaluation constraints

- Use sanitized Razorpay Test Mode fixtures for protocol/integration correctness.
- Use a replay harness for duplicate, delayed, invalid-signature, burst, and out-of-order behavior.
- Use seeded synthetic portfolios for statistical recovery evaluation.
- Mark every generated record and report as `SYNTHETIC`.
- Keep simulator ground truth hidden from the agent and scoring implementation.
- Split train/validation/held-out data by customer and time to reduce leakage.
- Freeze the held-out manifest before strategy tuning.
- Compare against no-action, immediate retry, fixed-delay retry, rules-only, and case-only baselines.
- Run ablations to measure the contribution of ML/agent ranking and portfolio intelligence.
- Report raw counts, uncertainty, model calibration, subgroup performance, business metrics, and safety metrics.
- Never claim simulated recovery percentage as a real merchant production result.

## 17. Code quality rules

- Keep the domain layer independent of FastAPI, Celery, LangGraph, provider SDKs, and frontend code.
- Prefer small, explicit application services over hidden lifecycle hooks and framework magic.
- Use typed interfaces/protocols for repositories, clocks, ID generation, providers, and model adapters.
- Inject clocks, random seeds, and external adapters so tests are deterministic.
- Keep side effects at the edges.
- Use structured error types and stable machine-readable reason codes.
- Do not catch broad exceptions unless adding context and re-raising or mapping to a deliberate failure state.
- Do not silently ignore errors.
- Do not leave placeholder success paths, hard-coded metrics, random recovery results, or untracked TODO behavior in completed work.
- Document non-obvious financial, concurrency, and safety decisions next to the relevant code or in an architecture decision record.
- Preserve existing user changes and avoid unrelated refactors.
- Do not add dependencies when the standard library or an existing dependency solves the task clearly.

## 18. Mandatory testing after every task

Every task that writes or changes a file must be verified before it is reported complete. Testing is part of implementation, not a separate optional phase.

### 18.1 Required workflow

After each task:

1. Inspect the final diff and confirm only intended files changed.
2. Format changed code with the repository formatter.
3. Run static analysis for the changed language.
4. Run type checking for the affected package.
5. Run the smallest relevant targeted test suite.
6. Add or update tests for every changed behavior and every fixed bug.
7. Run integration or end-to-end tests when the change crosses a database, queue, provider, API, worker, graph, or frontend boundary.
8. Run a production build when changing application wiring, dependencies, configuration, containers, or frontend code.
9. Re-read the relevant architecture invariants and confirm the change preserves them.
10. Report exactly which checks ran, their results, and any check that could not run.

Do not say “tested” when only code inspection, compilation, or formatting was performed. Do not report a task complete with a known failing relevant test.

### 18.2 Verification by change type

| Change | Minimum verification |
|---|---|
| Documentation only | Inspect rendered structure/links where possible; run repository markdown/link checks if configured |
| Python domain logic | Formatter, Ruff, mypy, targeted unit tests, property tests for affected invariants |
| FastAPI route/schema | Python checks, route tests, auth/tenant tests, OpenAPI/schema check |
| SQLAlchemy model/repository | Python checks, migration test, PostgreSQL integration tests, constraint/concurrency tests |
| Alembic migration | Upgrade clean DB, upgrade previous schema, verify data/constraints; downgrade only if project policy supports it |
| Webhook ingestion | Signature, invalid signature, duplicate, replay, raw-body, crash-boundary, and response tests |
| Queue/worker/outbox | Integration tests for retries, crash recovery, duplicate delivery, dead letter, and exactly-once effect |
| State machine/policy | All affected allowed/prohibited transitions, deterministic policy tests, counter/ceiling properties |
| Agent/LLM graph | Schema, timeout, malformed output, fallback, step-bound, redaction, and no-direct-action tests |
| Provider executor | Mock/contract tests, stable idempotency, timeout-to-`UNKNOWN`, reconciliation, and duplicate suppression |
| Portfolio intelligence | Normal baseline, spike detection, false-positive, isolation, resolution, and gradual-resume tests |
| Frontend | Formatter/lint/type check, component tests, production build, and browser flow for affected behavior |
| Evaluation logic | Fixed-seed reproducibility, leakage checks, baseline/metric unit tests, and report schema validation |
| Deployment/config | Config validation, container build, health/readiness smoke tests, and secret-leak scan |
| Security fix | Regression test that fails before the fix and passes after it, plus relevant security checks |

### 18.3 Full-suite requirements

Run the full repository test suite when:

- Changing shared domain types, state transitions, policy semantics, money utilities, event schemas, or database transactions.
- Changing dependency versions, build configuration, CI, containers, migrations, authentication, authorization, or tenant scoping.
- Completing a milestone or preparing a deployment/release.
- A targeted change has plausible cross-package impact.

If the full suite is too slow, run the affected suites during development and the full suite before declaring the milestone complete.

### 18.4 When tests cannot run

- Do not fabricate results or bypass a test.
- Explain the exact missing dependency, credential, service, fixture, or environment limitation.
- Run every safe check that does not require the missing item.
- Add a deterministic mock/simulator test when appropriate, while clearly distinguishing it from a real provider test.
- Leave the task incomplete if a required safety-critical verification cannot be performed.

## 19. Test invariants

The automated test program must continuously prove:

```text
policy violations in controlled scenarios      = 0
duplicate external business effects            = 0
unverified money counted as recovered          = 0
actions beyond retry/contact limits            = 0
cross-merchant data access                     = 0
silent loss of accepted valid events           = 0
```

Coverage percentage is useful but is not proof of correctness. Prioritize transition branches, failure boundaries, concurrency, database constraints, and unsafe negative cases.

## 20. Task completion checklist

A task is complete only when all applicable items are true:

- The requested behavior is implemented without unrelated changes.
- Acceptance criteria are met.
- The design follows `ARCHITECTURE.md` and preserves all invariants.
- New behavior and regressions have automated tests.
- Targeted tests, static checks, type checks, and builds pass as applicable.
- Cross-boundary changes have integration/end-to-end coverage.
- Database changes include tested Alembic migrations.
- Security, tenant isolation, audit, observability, and failure behavior were considered.
- Documentation and examples match actual behavior.
- No secret, personal data, fake success, or fabricated metric was introduced.
- The final response lists changed files, verification commands/results, and any remaining limitation.

Never optimize for merely appearing complete. RevenueGuard must fail honestly, stop safely, and make every financial claim verifiable.
