# RevenueGuard Contracts

This directory contains the versioned, implementation-neutral contracts shared by the API, workers, agent graph, evaluation harness, and dashboard.

## Current version

The current contract major version is `v1`; every instance uses `schema_version: "1.0"`.

| Contract | Schema | Canonical example |
|---|---|---|
| Revenue risk event | `v1/revenue-risk-event.schema.json` | `v1/examples/revenue-risk-event.example.json` |
| Recovery case | `v1/recovery-case.schema.json` | `v1/examples/recovery-case.example.json` |
| Decision receipt | `v1/decision-receipt.schema.json` | `v1/examples/decision-receipt.example.json` |
| Recovery action | `v1/recovery-action.schema.json` | `v1/examples/recovery-action.example.json` |
| Verified outcome | `v1/verified-outcome.schema.json` | `v1/examples/verified-outcome.example.json` |

The recovery state machine is defined in `v1/case-state-machine.json`.

## Rules

- Contracts use JSON Schema draft 2020-12.
- Unknown fields are rejected at system boundaries unless a future contract explicitly permits them.
- Money uses integer minor units and a three-letter uppercase currency code.
- Timestamps use RFC 3339 UTC strings.
- Identifiers are opaque stable strings; consumers must not derive business meaning from their format.
- Unsupported major versions fail safely and remain available for investigation/replay.
- Provider payloads are not internal contracts and must pass through normalization.

## Validation

Run the Phase 0 contract checks with:

```bash
python3 -m unittest tests.contract.test_phase0_contracts
```

These dependency-free tests validate JSON readability, schema metadata, canonical examples, money rules, version rules, state transitions, decision enums, action idempotency, and authoritative recovery accounting.
