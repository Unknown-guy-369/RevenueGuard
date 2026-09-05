# Held-out evaluation dataset: version 1

This directory contains sealed, **SYNTHETIC** evaluation scenarios for RevenueGuard. It is neither training data nor an executable replay fixture. No production identifiers, contact data, secrets, or real merchant outcomes are present.

## Contract

- `manifest.json` is the index. It is intentionally marked `sealed: true`.
- Every scenario is standalone JSON and is named by its stable `scenario_id`.
- `input` contains only facts observable at the stated boundary. `expected` is an oracle for a future isolated evaluator; no application, prompt, or model may consume it.
- `negative_assertions` are mandatory safety checks. A superficially successful state cannot override one of these failures.
- This directory has no runner and is not registered with test discovery or CI.

## Canonical content hash

`content_hash` uses SHA-256 and is calculated from scenario files only. For every listed scenario path in ascending byte order, canonicalize its JSON by recursively sorting object keys, serializing with no whitespace and UTF-8, calculate that file's SHA-256 hex digest, then hash the UTF-8 sequence:

```text
relative-path + NUL + scenario-json-sha256 + LF
```

The manifest's own contents are excluded so the seal does not recursively hash itself. Any scenario change requires a new sealed directory version rather than silently modifying this suite.

## Scenario fields

Every file has `scenario_id`, `classification`, `dataset_role`, `category`, `input`, `expected`, `negative_assertions`, and `invariant_tags`. Money is always integer minor units plus ISO currency; times are UTC ISO-8601. All IDs begin with `syn_`.

The `expected` object may contain case transitions, policy results, model-boundary output/fallback, durable action records, incident lifecycle, and verified financial metrics. An omitted record is not authorization to invent one: the scenario's negative assertions and explicit arrays define the boundary.

## Coverage

The manifest indexes 29 representative safety scenarios. It is deliberately not exhaustive across every Cartesian product of provider error, policy version, and time. It covers the system's core workflows and the boundaries most likely to create unsafe financial behavior: ingestion, recovery, deterministic policy, LLM diagnosis, execution and reconciliation, customer coordination, portfolio incidents, and tenant isolation.
