# Sealed Held-Out Evaluation Dataset Design

## Status and scope

This design specifies a **synthetic, sealed, held-out evaluation dataset** for
RevenueGuard. It is an evaluation artifact, not training data, a demo dataset,
or a production replay source. Its purpose is to measure whether the system
preserves financial-workflow invariants across the workflows the product is
designed to support.

The requested deliverable is dataset content and its validation contract only.
It must not add a command that executes scenarios, register a test suite, add a
CI job, call a provider, start a worker, or automatically replay any event. A
future, explicit request may add a read-only evaluation runner which consumes
this dataset. That runner is out of scope for this change.

Every record, fixture, expected outcome, and aggregate baseline in this suite
is labelled `SYNTHETIC`. No scenario may contain production merchant data,
personal contact data, credentials, or a claim about real recovery results.

## Goals

- Cover the core recovery workflows: failed subscription, payment degradation,
  and B2B receivables/promise-to-pay.
- Cover the event, policy, LLM, execution, verification, coordination, and
  merchant-portfolio boundaries that can produce unsafe financial behavior.
- Make expected behavior explicit and machine-readable, including both
  positive expected results and safety-negative assertions.
- Preserve a strict separation between the sealed held-out set and ordinary
  developer fixtures so implementation cannot be tuned to this suite.
- Allow later reproducible evaluation without embedding runtime behavior into
  the dataset itself.

## Non-goals

- Training, fine-tuning, prompt optimization, or few-shot examples for a
  model.
- Simulating a real merchant, Razorpay production behavior, or a customer
  communication channel.
- Creating random outcomes, fake success, or a recovery-rate claim.
- Replacing focused unit/integration fixtures that exercise day-to-day
  development behavior.
- Modifying existing Phase 6 or Phase 7 implementation, migrations, workers,
  policies, or tests.

## Placement and layout

The suite will be added beneath `fixtures/evaluation/held_out_v1/`, separate
from the existing `fixtures/razorpay/datasets/agent_batch_10_v1/` normalization
fixture batch. The latter stays an ordinary synthetic webhook batch; it is not
retroactively declared held-out.

```text
fixtures/evaluation/held_out_v1/
  manifest.json
  README.md
  scenarios/
    ingestion/
    recovery/
    policy/
    llm_boundary/
    execution/
    coordination/
    portfolio/
    tenancy/
```

Each scenario is a standalone JSON document. This keeps scenario diffs small,
allows a reviewer to trace a failure to one safety contract, and avoids a
single monolithic fixture whose changes are difficult to audit. `README.md`
defines the schema, seal procedure, and coverage matrix but does not contain a
command to run the suite.

## Dataset contract

`manifest.json` is the only suite index. It must contain:

- `classification: "SYNTHETIC"` and `dataset_role: "HELD_OUT_EVALUATION"`;
- an immutable `dataset_version`, schema version, and UTC creation timestamp;
- `sealed: true` and a canonical SHA-256 `content_hash` over the scenario
  paths and canonical JSON content;
- a non-empty list of scenario IDs, paths, categories, and invariant tags;
- fixed synthetic baseline metadata and no production-performance fields;
- a coverage summary that agrees with the individual scenario metadata.

Each scenario document must have this conceptual shape:

```json
{
  "scenario_id": "HOV1-PORTFOLIO-INCIDENT-001",
  "classification": "SYNTHETIC",
  "dataset_role": "HELD_OUT_EVALUATION",
  "category": "portfolio",
  "title": "Correlated issuer outage defers only affected retries",
  "input": {
    "authoritative_state": {},
    "events": [],
    "provider_observations": [],
    "merchant_policy_snapshot": {},
    "permitted_model_evidence": {}
  },
  "expected": {
    "case_transitions": [],
    "policy_results": [],
    "model_boundary": {},
    "actions": [],
    "incident": {},
    "metrics": {}
  },
  "negative_assertions": [],
  "invariant_tags": []
}
```

All monetary values are non-negative integer minor units with an explicit ISO
currency. All datetimes are explicit UTC ISO-8601 values. Identifiers use
synthetic namespaces (`syn_merchant_*`, `syn_case_*`, and similar) and never
reuse a real Razorpay identifier.

The input section is limited to facts that would be available at the specified
boundary. It may never include hidden simulator ground truth, post-decision
provider state, an implicit policy override, or a secret. Expected data is
assertion data, never an instruction that an LLM or executor can consume.

## Scenario families

The first release will contain a compact but representative matrix. It should
prefer high-value boundary cases over trying to enumerate every permutation.
Each family includes positive and failure-path cases.

| Family | Minimum scenarios | Required assertions |
| --- | --- | --- |
| Ingestion | valid accepted event; invalid signature; duplicate; delayed/out-of-order terminal event; burst; unsupported payload | raw-body verification, durable duplicate recognition, no state regression, preserved unsupported event, no silent loss |
| Recovery | failed subscription; payment degradation; invoice overdue/promise-to-pay; already-paid/cancelled; disputed | correct correlation/diagnosis candidates, allowed state path, only a verified recovery is counted |
| Policy | one case for each of `PROCEED`, `DEFER`, `SKIP`, `STOP`, `REQUIRE_HUMAN`; retry ceiling; quiet hours; opt-out; minimum economics | exact result and reason code, action eligibility, no policy bypass |
| LLM boundary | valid schema-conforming diagnosis assistance; malformed JSON; schema-invalid answer; provider timeout; redaction input; forbidden proposed action | typed result or deterministic fallback, version recording, no PII/secret in model evidence, no direct action authority |
| Execution | stable logical idempotency across redelivery; provider timeout; unknown-action block; later reconciliation success/failure; verification mismatch | one external business effect, `UNKNOWN` not guessed, equivalent action blocked, verified-only money metric |
| Coordination | same customer with two active workflows; missing canonical customer ID; active/unknown contact intervention | aggregate contact limit and contact owner respected, safe deferral/skip, unrelated retry remains eligible |
| Portfolio | normal baseline; correlated spike; affected-case deferral; unrelated dimension remains eligible; clear-window resolution; gradual resume | tenant-scoped incident, threshold evidence, targeted constraint, re-evaluation before resume, no retry storm |
| Tenancy | identical provider-shaped IDs across two merchants; merchant-scoped incident and customer coordination | no cross-merchant read, action, incident, or financial metric |

## LLM-boundary representation

The dataset does not store a prompt for a production model and must never
capture a real model response. Instead, each LLM case provides a sanitized,
typed `permitted_model_evidence` object and one of two expected results:

1. A schema-valid structured diagnosis assistance result with candidate types
   that remain subject to the deterministic policy engine.
2. A named deterministic fallback result for malformed output, timeout,
   provider failure, invalid JSON, or schema rejection.

Expected LLM data cannot contain action execution details, money mutations,
customer contact requests, provider credentials, or a result that marks a case
recovered. The expected assertion always checks that the deterministic policy
is the authorization source and that the model/model-schema/prompt-or-template
versions are retained in the decision receipt.

## Portfolio-incident contract

Portfolio scenarios use fixed, transparent synthetic observations: a baseline
window, a current window, a dimension (for example issuer family plus failure
category), and the threshold version. They must explicitly cover:

1. Normal behavior below the threshold: no incident.
2. A correlated failure spike: one tenant-scoped active incident with evidence
   and only the affected case population linked to it.
3. Policy behavior while active: affected retry/contact actions defer, while an
   unrelated merchant or unrelated incident dimension continues normally.
4. Resolution after the configured consecutive clear windows.
5. Durable staggered `resume_after` values and current-policy re-evaluation
   before any resumed action is authorized.

No portfolio scenario may treat an incident as evidence of a payment outcome
or as a reason to count money recovered.

## Expected assertions and safety-negative assertions

Expected behavior is divided to make safety failures impossible to hide behind
a superficially successful final state:

- `case_transitions` lists ordered, valid state transitions and associated
  reason/evidence references.
- `policy_results` lists exactly one deterministic result for each examined
  candidate, including a machine-readable reason code.
- `actions` names only expected durable authorization/attempt/outcome records;
  an external effect is never expected without a stable logical idempotency
  key and final policy re-evaluation.
- `metrics` independently states recovered gross, cost, and net minor units;
  recovery must remain zero unless authoritative verification is included.
- `negative_assertions` names prohibited effects, such as
  `NO_DUPLICATE_EXTERNAL_EFFECT`, `NO_UNVERIFIED_RECOVERY`,
  `NO_CROSS_MERCHANT_ACCESS`, `NO_LLM_DIRECT_ACTION`, and
  `NO_ACTION_WHILE_EQUIVALENT_UNKNOWN`.

The required suite-wide totals are always zero for policy violations,
duplicate external business effects, unverified recovery counted as money,
actions past retry/contact limits, cross-merchant access, and silent loss of
accepted valid events.

## Seal, governance, and change control

The suite is held out by governance, not merely its directory name:

- No application module, model adapter, agent prompt/template, scoring code, or
  production test may import the expected outcomes.
- The manifest's content hash is recomputed only by a future explicit sealing
  workflow. Editing any scenario without updating the seal is an invalid suite
  state, not an implicit update.
- Any intentional dataset revision creates a new immutable version directory
  (`held_out_v2`), explains the reason, records what changed, and preserves
  the previous suite.
- Held-out scenario IDs and expected results must not be copied into developer
  fixture comments, prompt examples, model-training material, or dashboard
  demonstrations.
- Evaluations report the dataset version/hash, software revision, policy/model/
  feature versions, environment, raw counts, and safety metrics. They must
  state `SYNTHETIC` and must not present results as production performance.

## Validation and future execution boundary

This dataset change itself receives documentation and structural verification
only: validate all JSON documents, verify every manifest path is local and
unique, verify synthetic classification and invariant coverage, recompute and
compare the manifest content hash, and inspect the final diff. These checks
must be explicit one-off validation commands or tests added only when the user
later authorizes them; they are not an execution runner.

A later evaluation runner must be separate from the fixture content. It may
only read inputs and expected assertions, use deterministic adapters/mocks,
make no real provider or customer-contact call, preserve all data in an
isolated test database, and require a human to invoke it explicitly. Before
that work starts, it needs a separate design and approval because it crosses
database, queue, provider-adapter, model-boundary, and evaluation-reporting
boundaries.

## Acceptance criteria

- The dataset is fully synthetic, sealed, versioned, self-describing, and
  structurally separate from ordinary fixtures.
- The manifest and every scenario encode only test inputs and expected
  assertions; they do not execute any behavior.
- The matrix covers every core workflow and every policy outcome, plus the
  specified LLM, execution, coordination, incident, and tenant-isolation
  boundaries.
- Each scenario records enough expected detail to prove the financial and
  safety invariants, including verified-only recovery accounting.
- The change adds no runner, CI registration, default test discovery, worker
  task, scheduled activity, real credential, or real external request.
