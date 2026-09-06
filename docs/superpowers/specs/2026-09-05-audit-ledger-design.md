# Forward-Only Audit Ledger Design

## Status

Approved design pending user review. This document specifies a new audit ledger for records
created after its migration. Existing records are not backfilled, rewritten, or represented as
historical audit entries.

## Objective

RevenueGuard needs an authoritative, merchant-scoped, append-only, tamper-evident record of each
material workflow change. The ledger must make it possible to prove the ordered history of new
events, decisions, authorizations, executions, outcomes, and human interventions without allowing
an LLM, dashboard, queue, or background process to repair or overwrite history.

## Chosen approach

Use an application-level PostgreSQL ledger. Every material write appends an audit entry in the
same SQLAlchemy transaction as the domain mutation. A merchant-specific ledger-head row is locked
while appending, which produces one linear chain per merchant even when workers race.

Database triggers were rejected because they cannot construct clear, redacted, version-aware
business receipts. Event-sourcing was rejected because retrofitting the existing authoritative
relational workflow would add unacceptable migration and replay risk.

## Data model

`audit_ledger_heads`

- Primary key: `merchant_id`.
- `latest_sequence` and `latest_entry_hash` identify the current chain head.
- The row is created lazily with a transactional genesis entry. It is never updated except by the
  audited append service.

`audit_entries`

- Composite primary key: `merchant_id`, `sequence`.
- Immutable identifiers: `entry_id`, `correlation_id`, `causation_id`, `actor_type`, and
  `actor_reference`.
- Domain classification: `event_type`, `aggregate_type`, and `aggregate_id`.
- Reproducibility fields: policy, model, prompt, schema, feature, and application versions when
  applicable.
- `payload` is a canonical, redacted summary; `payload_sha256` binds its exact canonical encoding.
- `previous_entry_hash` plus `entry_hash` create a SHA-256 chain.
- `recorded_at` is UTC server time. No update or delete application method exists.

`entry_hash` is SHA-256 of a canonical JSON document containing the merchant, sequence, immutable
metadata, canonical payload digest, and prior hash. The encoding has sorted keys, UTF-8, compact
separators, no floats, and explicit ISO-8601 UTC timestamps.

## Forward-only boundary

The migration creates empty ledger tables. The first post-migration entry for a merchant is
`LEDGER_GENESIS` at sequence 1 with an all-zero previous hash. It declares that verification covers
entries from this ledger start only. Existing workflow records remain valid operational data but do
not gain retroactive tamper-evident claims.

## Append contract

The `AuditLedger` application service accepts an immutable, typed append request. It:

1. validates the event category and the redacted payload;
2. creates or locks the merchant head with `SELECT ... FOR UPDATE`;
3. calculates the next sequence and hashes from canonical bytes;
4. inserts the entry and advances the head in the current transaction.

If the append fails, the caller's business transaction fails and rolls back. There is no deferred
audit queue, retry-only audit writer, or successful mutation without an entry.

The first implementation wires entries to these material write seams:

- accepted or rejected webhook-ingestion disposition;
- recovery-case transition;
- decision receipt and policy result;
- recovery-action authorization and each provider attempt;
- authoritative or uncertain outcome verification;
- human-review request and decision;
- portfolio-incident opening, policy effect, and resolution.

Read-only dashboard queries, cache updates, worker leases, and view refreshes do not create audit
entries.

## Privacy and authorization

Payloads contain stable identifiers, amounts in integer minor units, currency, state/reason codes,
and references to authoritative records. They must never include raw webhook bodies, card data,
secrets, authorization headers, unredacted customer contact details, or LLM prompts. All reads and
verification are merchant scoped. Cross-merchant verification requests fail closed.

## Verification

`AuditLedgerVerifier` is read-only. Starting from the genesis entry, it recomputes payload and entry
hashes, checks sequence continuity and head agreement, and returns either `VALID` or the first
broken sequence with one of: `MISSING_GENESIS`, `SEQUENCE_GAP`, `PREVIOUS_HASH_MISMATCH`,
`PAYLOAD_HASH_MISMATCH`, `ENTRY_HASH_MISMATCH`, or `HEAD_MISMATCH`.

The verifier never repairs records, changes the head, or treats an incomplete chain as valid.

## Failure behavior

- Contention serializes per merchant; unrelated merchants continue independently.
- Database failure rolls back both the business write and audit append.
- An unavailable verifier does not alter workflow state, but operators cannot claim verified audit
  integrity until it succeeds.
- Audit writes preserve `UNKNOWN` execution facts; they never infer success from a timeout.

## Test plan

Tests are written at public seams:

1. Ledger append and verify succeeds for a known canonical chain.
2. Mutating a payload, hash, sequence, or head produces the correct first-failure result.
3. Concurrent appends for one merchant are contiguous and single-chain; two merchants remain
   isolated.
4. An induced append failure rolls back the associated domain transaction.
5. A recovery-action execution tracer emits ordered authorization, attempt, and authoritative
   outcome entries.
6. Tenant-scoped API verification and export reject another merchant's ledger.
7. Migration upgrades a clean database and an existing pre-ledger database without backfill.

## Acceptance criteria

- Every newly created material workflow record has a same-transaction audit entry.
- No business write is committed when its required audit append fails.
- The verifier detects deliberate tampering deterministically and names the first bad sequence.
- The ledger begins forward-only per merchant and is never described as covering prior records.
- The dashboard/export presents the ledger as tamper-evident, not absolutely immutable.
