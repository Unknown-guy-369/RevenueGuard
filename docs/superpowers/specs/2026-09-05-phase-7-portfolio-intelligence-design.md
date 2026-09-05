# Phase 7 Portfolio Intelligence and Coordination Design

## Status and scope

This design implements Phase 7 of `IMPLEMENTATION_PLAN.md`. It builds on the Phase 6
payment-degradation records, portfolio incidents, policy incident constraints, and staggered
resume timestamps already present in the repository.

Phase 7 covers backend domain logic, PostgreSQL persistence, worker scheduling, and automated
verification. Dashboard expansion, audit hash chaining, and broader observability remain Phase 8
work.

The implementation must preserve the repository invariants: PostgreSQL is authoritative, policy
is deterministic and final, customer contact and payment actions never originate from an LLM,
tenant scope is mandatory, external effects remain idempotent, and simulated model evidence is
never represented as production evidence.

## Existing foundations

The current codebase already provides:

- normalized payment outcome observations;
- transparent baseline-versus-current degradation assessments;
- durable portfolio incidents and incident-to-case links;
- policy deferral for active incident constraints;
- durable deferred-case wake times;
- case-level candidate generation and deterministic policy evaluation;
- action outbox authorization and pre-execution policy re-evaluation.

Phase 7 will extend these boundaries rather than introduce a parallel incident or action system.

## Architecture

### Portfolio evaluation service

A dedicated portfolio application service will coordinate merchant-scoped aggregation and
incident maintenance. It will support two entry points over the same idempotent operation:

1. A streaming entry point invoked after a new payment observation is stored.
2. A scheduled sweep that evaluates merchants with recent observations, including merchants whose
   incident may now be eligible for resolution even when no new failure arrives.

Both paths use the existing transparent window calculation and threshold version. A merchant-level
database lock serializes incident lifecycle changes. An active incident is unique per merchant and
dimension. Repeated evaluations update evidence and extend the monitoring window rather than
creating duplicates.

Incident resolution requires the configured number of consecutive clear windows. Resolution sets
durable, ordered `resume_after` values on affected case links. Deferred cases are released in
bounded batches and must run the current policy before any new action is authorized.

### Customer identity and contact governor

Cross-workflow identity is the canonical internal key `(merchant_id, customer_id)`. Provider IDs,
payment IDs, subscription IDs, invoice IDs, email addresses, and phone numbers are not independent
coordination identities.

Before a case containing any customer-contact candidate is evaluated, the recovery service locks
the tenant-scoped customer row. It then obtains a customer contact snapshot across all active cases
for that customer. The snapshot contains:

- active case identifiers;
- the aggregate number of executed contact attempts;
- whether a contact action is pending, executing, awaiting verification, or unknown;
- the action and case that currently own the intervention, when one exists.

The policy input uses the aggregate customer contact count rather than only the current case count.
An existing active contact causes deterministic deferral with
`CUSTOMER_CONTACT_ALREADY_IN_PROGRESS`. Because the customer lock, policy evaluation, decision
receipt, and action-outbox insert occur in one transaction, concurrent playbooks cannot authorize
two independent contacts.

The selected action records the coordinated case identifiers in its non-sensitive parameters. The
active intervention record retains the same identifiers. This creates one inspectable intervention
without pretending case IDs are source evidence and without merging or destroying the independently
auditable recovery cases.

Customers without a canonical `customer_id` cannot receive an automated customer-contact action;
such candidates are skipped safely while internal, retry, escalation, stop, and no-action candidates
remain eligible.

### Recovery probability model

Phase 7 introduces a framework-independent, versioned logistic recovery scorer. Inference uses a
fixed ordered feature schema, integer basis-point inputs where practical, and an explicit coefficient
artifact. The initial features are limited to facts already available at decision time:

- normalized amount bucket;
- retry count;
- aggregate customer contact count;
- diagnosis confidence;
- action type;
- normalized failure category;
- active systemic-incident signal;
- hour and day-of-month buckets.

The model returns a bounded recovery probability in basis points. No model output changes amount,
currency, consent, retry ceilings, contact ceilings, incident constraints, or approval requirements.

The bundled demonstration coefficient artifact is labelled `SYNTHETIC`, includes its model and
feature versions, and is never described as merchant-calibrated. The model boundary accepts a
different validated artifact later, after the Phase 10 held-out evaluation proves calibration and
business value.

Malformed artifacts or inference failures use the existing conservative candidate probabilities and
record a machine-readable fallback reason. They do not bypass policy or fabricate success.

### Economic ranking

For each candidate, the decision governor calculates in integer minor units:

```text
expected gross recovery = floor(probability_basis_points * amount_minor / 10_000)
expected net recovery   = expected gross recovery
                          - action_cost_minor
                          - risk_penalty_minor
                          - customer_friction_penalty_minor
```

Penalty values are versioned configuration, keyed by action type, and constrained to non-negative
integers. Candidates are ranked by descending expected net recovery, then descending recovery
probability, then action type for deterministic tie-breaking. `NO_ACTION` remains the final fallback.

Only action types allowed by the immutable merchant policy snapshot enter economic ranking, except
for escalation, stop, and no-action candidates that policy always needs for safe fallback. The full
policy engine still evaluates the ranked list using current consent, incidents, terminal facts,
unknown actions, promises, quiet hours, limits, and approvals. The executor repeats policy evaluation
before any external effect.

Decision receipts retain the ordered candidates, computed probabilities and expected net values,
model version, feature version, policy version, and the selected policy result.

## Persistence changes

An Alembic migration will add only the durable structures and indexes needed for Phase 7
coordination:

- a customer-contact lookup index joining merchant/customer cases to active actions efficiently;
- a `customer_interventions` table and active-intervention uniqueness constraint;
- an index for bounded intervention-expiry maintenance.

Scoring does not require a new table. Candidate JSON already stores probabilities and expected net
values, while the receipt version bundle will be extended to store the scorer version, feature
version, artifact classification, and optional fallback reason. These are immutable receipt values.

The `customer_interventions` table is keyed by merchant and intervention ID. It contains the
canonical customer ID, owning case and action, coordinated case IDs, status, `cooldown_until`,
model/policy versions, and timestamps. A partial unique index allows at most one `ACTIVE`
intervention per `(merchant_id, customer_id)`. A separate link table is unnecessary because the
coordinated case IDs are a frozen JSON list and every owner reference uses a tenant-scoped foreign
key. Historical intervention rows are retained for auditability.

A successful contact keeps the intervention active until `cooldown_until`; a failed action may be
closed immediately. An `UNKNOWN` action never releases the intervention automatically. Scheduled
maintenance closes only expired interventions whose owning action has a non-unknown terminal
status. This prevents a second playbook from contacting the customer immediately after the first
message or while delivery is ambiguous.

All foreign keys include merchant scope. No runtime `create_all` behavior is added. The migration is
forward-safe; downgrade will refuse to erase financial workflow history if doing so would discard
coordination records.

## Worker behavior

Celery Beat will add a portfolio-maintenance task. Each run:

1. Claims a bounded set of merchant scopes with relevant recent observations or active incidents.
2. Re-evaluates incident dimensions under merchant locks.
3. Resolves incidents only after clear-window criteria are met.
4. Makes only the configured number of incident-linked deferred cases due per run.
5. Leaves unrelated cases eligible throughout the incident.

Streaming observation processing continues to evaluate the affected merchant immediately. The
scheduled path closes the failure mode where an incident never resolves because event traffic stops.
Task results report counts only; PostgreSQL records remain authoritative.

## Failure and concurrency behavior

- Duplicate observations do not create duplicate assessments, incidents, interventions, receipts,
  or actions.
- Concurrent cases for one customer serialize on the customer row and the database uniqueness rule.
- A worker crash before commit leaves no partial intervention; a crash after commit leaves a durable
  intervention and action outbox row.
- Expired or terminal interventions are released only after authoritative action state is checked.
- `UNKNOWN` contact outcomes continue to block equivalent or additional contact.
- Incident resolution never directly executes an action; it only schedules bounded re-evaluation.
- Missing customer identity blocks automated contact instead of guessing identity.
- Cross-merchant identity values never coordinate or constrain one another.
- Model errors preserve safe fallback candidates and are visible in versioned decision metadata.

## Verification plan

### Domain tests

- logistic inference validation, feature ordering, bounds, and deterministic output;
- integer expected-value arithmetic, penalty application, stable ordering, and currency preservation;
- policy-incompatible candidates excluded from ranking while safe fallbacks remain;
- malformed or unavailable model artifact uses the conservative fallback;
- no floating-point money calculations.

### Persistence and concurrency tests

- migration upgrade from the current schema and upgrade from a clean database;
- one active customer intervention under concurrent transactions;
- customer contact counts aggregate across playbooks and cases;
- tenant isolation for observations, incidents, cases, interventions, and actions;
- unknown and in-progress contact outcomes block a second intervention;
- duplicate streaming and scheduled evaluations remain idempotent.

### Portfolio integration tests

- a seeded synthetic systemic spike defers only correlated cases;
- unrelated failure dimensions continue normally;
- multiple cases for one customer create one coordinated intervention;
- incident resolution requires consecutive clear windows;
- scheduled evaluation resolves a quiet incident without a new failure event;
- bounded resume batches and spaced wake times prevent a retry storm;
- resumed cases re-evaluate current policy before authorization;
- restart tests prove incidents, interventions, and wakeups are durable.

### Required repository checks

- Ruff formatting and linting;
- strict mypy;
- targeted domain and Phase 7 tests;
- PostgreSQL migration and integration tests;
- full Python test suite because the change affects shared decision semantics, policy inputs,
  transactions, and migrations;
- production container/build checks if worker wiring or dependencies change;
- final diff and invariant review.

## Acceptance criteria

Phase 7 is complete when automated tests demonstrate all of the following:

1. A simulated systemic spike creates or updates a versioned incident and defers correlated cases.
2. Unrelated cases continue through ordinary decisioning.
3. Multiple active cases for one customer can authorize only one coordinated contact intervention.
4. Expected net recovery ordering is deterministic, integer-based, versioned, and policy-constrained.
5. Incident resolution requires explicit clear evidence and resumes eligible cases in bounded,
   staggered batches.
6. Every resumed or previously authorized action passes current policy before external execution.
7. Duplicate effects, policy violations, unverified recovered money, limit overruns, cross-merchant
   access, and silent accepted-event loss remain zero in controlled tests.
8. Synthetic scoring artifacts and outcomes are visibly labelled synthetic.
