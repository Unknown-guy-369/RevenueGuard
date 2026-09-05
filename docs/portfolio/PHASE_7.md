# Phase 7 portfolio intelligence and coordination

Phase 7 turns the existing case-level workflows into a merchant-scoped portfolio control loop.
It does not add another execution path: deterministic policy, the durable action outbox, the
provider executor, and authoritative outcome verification remain mandatory.

## Portfolio incident lifecycle

Each normalized payment success or failure becomes a tenant-scoped, idempotent observation. The
same transparent integer-basis-point detector runs in two places:

- the event worker evaluates the affected merchant immediately after storing a payment outcome;
- Celery Beat runs `revenueguard.portfolio.maintain` every minute so an incident can clear even
  when no new failure arrives.

An incident is unique by merchant, payment method, issuer family, error family, and threshold
version. A qualifying spike creates or refreshes it. Correlated retry and money-intent candidates
are deferred; unrelated dimensions and other merchants continue normally. Two consecutive clear
windows resolve the incident. Linked cases receive deterministic 30-second staggered wake times
and re-enter the ordinary policy path in bounded worker batches.

## Cross-case customer contact governor

The coordination identity is `(merchant_id, customer_id)`. Before authorizing contact, the
recovery transaction locks that tenant-scoped customer and reads every active case and executed
contact count for the customer.

The first allowed contact creates both the outbox action and an `ACTIVE` customer intervention in
the same PostgreSQL transaction. Later playbooks append their case ID to that intervention and
defer with `CUSTOMER_CONTACT_ALREADY_IN_PROGRESS`; they do not create another contact. A database
partial unique index is the final concurrency guard against two active interventions.

An `UNKNOWN` owning action keeps the intervention active. A failed contact can be released by
maintenance, and a successful contact remains active until its cooldown expires. Cases without a
canonical customer ID cannot authorize automated contact.

## Expected-net decision ranking

The decision governor ranks policy-compatible candidates using integer minor units:

```text
expected gross = floor(probability_basis_points * amount_minor / 10_000)
expected net   = expected gross - action cost - risk penalty - friction penalty
```

Probability inference uses a versioned logistic coefficient artifact and a fixed feature schema.
The bundled coefficients and default economics are explicitly labelled `SYNTHETIC`; they make the
workflow deterministic and testable but are not merchant-calibrated production evidence. A
`PRODUCTION` artifact must come from the later held-out evaluation and calibration gate. Decision
receipts retain model, feature, economics, artifact-classification, probability, penalty, and
expected-net evidence.

Economic ranking cannot turn a forbidden action into an allowed action. Candidate types are
filtered by the immutable policy snapshot before ranking, the full policy engine evaluates the
ranked list, and execution repeats current policy immediately before any external effect.

## Configuration

```text
REVENUEGUARD_PORTFOLIO_MAINTENANCE_MERCHANT_BATCH_SIZE=50
REVENUEGUARD_CUSTOMER_INTERVENTION_MAINTENANCE_BATCH_SIZE=100
REVENUEGUARD_DEFERRED_CASE_REEVALUATION_BATCH_SIZE=50
```

The worker must consume the `portfolio_maintenance` and `case_reevaluation` queues. The repository
Make target, worker container, and `.env.example` include those queues and safe bounded defaults.

## Verification map

- Scoring arithmetic, stable ranking, artifact labels, and intervention contracts:
  `tests/unit/test_portfolio_intelligence.py`.
- Aggregate contact limits, tenant isolation, one coordinated intervention, `UNKNOWN` handling,
  scheduled incident resolution, and durable scoring evidence:
  `tests/integration/test_phase7_portfolio.py`.
- Correlated deferral, unrelated-case isolation, two-clear-window resolution, and staggered resume:
  `tests/integration/test_phase6_playbooks.py`.
- Streaming observation extraction and scheduled worker registration:
  `apps/worker/tests/test_event_tasks.py` and `apps/worker/tests/test_playbook_tasks.py`.
- Clean and incremental schema upgrades: `tests/integration/test_phase3_migrations.py`.

## Deliberate limitations

- The built-in scorer is a synthetic control artifact, not a production recovery model.
- Portfolio and intervention views in the operator dashboard remain Phase 8 work.
- Statistical held-out comparison, calibration reporting, and production promotion remain Phase 10
  evaluation work; until then the synthetic classification must remain visible.
