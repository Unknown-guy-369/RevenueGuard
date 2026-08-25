# RevenueGuard Implementation Memory

## Purpose

This file records durable, repository-level implementation context for future RevenueGuard tasks. It contains no secrets, raw customer data, or unverified recovery claims.

## Current status

- Completed milestone: Phase 3 — recovery domain and deterministic policy decisioning.
- Previous milestones: Phase 1 scaffold and Phase 2 event ingestion/persistence.
- Money movement, customer contact, action execution, outcome verification, and recovered-revenue accounting remain disabled.
- Razorpay integration is restricted to Test Mode fixtures and authenticated webhook ingestion.

## Phase 2 acceptance checklist

- [x] Verify Razorpay HMAC-SHA256 signatures over the unmodified request body.
- [x] Persist accepted provider events before returning `2xx`.
- [x] Collapse five identical deliveries into one logical accepted/normalized event.
- [x] Record invalid-signature forensic metadata without queueing or retaining the untrusted body.
- [x] Normalize supported fixtures into the versioned `RevenueRiskEvent` contract.
- [x] Preserve merchant-scoped customer, payment, subscription, and correlation identities.
- [x] Recover dispatch after an API/worker crash without losing the inbox event.
- [x] Bound retries and retain exhausted work in an observable dead-letter state.
- [x] Provide normal, duplicate, invalid-signature, delayed, burst, and out-of-order replay modes.
- [x] Pass migration, contract, tenant-isolation, targeted, and full repository verification.

## Phase 3 acceptance checklist

- [x] Enforce the canonical recovery-case state graph in domain code and PostgreSQL.
- [x] Require authoritative evidence before a case can transition to `RECOVERED`.
- [x] Diagnose supported failures and rank deterministic, reproducible candidates.
- [x] Apply versioned, currency-bound merchant policy with consent, ceiling, incident, quiet-hour, expected-value, unknown-outcome, cross-workflow contact, and human-review guardians.
- [x] Persist immutable policy versions, transitions, decision receipts, evidence links, and action-bound human reviews.
- [x] Process normalized events, due deferred cases, and human decisions transactionally without initiating Phase 4 effects.
- [x] Run recovery decisioning before worker dispatch completion in the normalization transaction.
- [x] Prove replay/crash idempotency, tenant isolation, optimistic concurrency, provider-truth ordering, and append-only database enforcement.
- [x] Validate both clean and `0003 -> 0004` migrations in disposable databases.

## Architecture and safety decisions

- PostgreSQL is authoritative; Redis/Celery carries disposable at-least-once delivery state.
- The webhook request performs authentication and durable inbox storage only.
- Valid-event deduplication is scoped by `(provider, merchant_id, provider_event_id)` in PostgreSQL.
- Invalid signatures cannot poison the valid-event deduplication key and never reach normalization.
- The inbox transaction creates a durable dispatch record; broker publication is recoverable and is not part of the webhook acknowledgement boundary.
- Provider occurrence time, system receive time, and system processing time remain distinct.
- Phase 2 creates no recovery action and counts no recovered money.
- Phase 3 stops at `READY`; it creates no action outbox row and cannot enter `EXECUTING`.
- Every policy evaluation records the exact policy/application/schema/feature versions and complete candidate action identity.

## Implemented components

- FastAPI raw-body Razorpay gateway with bounded payload reads, merchant resolution, constant-time signature verification, durable response semantics, duplicate receipts, and hash-only rejection records.
- SQLAlchemy persistence for merchants, customers, payments, subscriptions, exact webhook bodies, normalized events, typed correlations, and durable dispatch leases.
- Alembic revisions `0002_phase2_event_ingestion` and `0003_phase2_dead_letter_replay`; downgrade intentionally refuses to erase accepted workflow history.
- Framework-independent `RevenueRiskEvent` plus strict Razorpay normalizer for nine allowlisted event types and machine-readable malformed/unsupported failures.
- Celery beat dispatcher and ingestion worker with at-least-once idempotency, bounded retry/backoff, expired-lease recovery, dead-letter retention, and explicit operator replay metadata.
- Four sanitized `SYNTHETIC` Razorpay-shaped fixtures, normalized contract snapshots, six-mode HTTP replay CLI, merchant bootstrap CLI, and dead-letter requeue CLI.
- README, dashboard status, environment template, Make targets, locked dependencies, and API/worker container wiring updated for Phase 2.
- Phase 3 recovery-case, diagnosis, policy, review, persistence, service, migration, worker integration, and conservative merchant-policy bootstrap are implemented.

## Verification ledger

- `make check` — passed: Ruff format/lint, strict mypy (32 source files), 69 Python tests plus 36 contract subtests, frontend Prettier/ESLint/TypeScript, 1 Vitest test, Next.js production build, and Docker Compose validation.
- `uv run pytest -q tests/integration/test_phase2_persistence.py` — passed against PostgreSQL: 4 tests covering tenant constraints, exact raw bytes, invalid-signature isolation, five-delivery deduplication, provider ordering, normalization idempotency, leases, retry/dead letter/replay, and API-to-worker persistence.
- `uv run alembic upgrade head`, `uv run alembic current`, and `uv run alembic check` — passed from the previous schema; head is `0003_phase2_dead_letter_replay` with no pending model operations.
- Disposable clean database `base -> 0001 -> 0002 -> 0003` migration — passed and the disposable database was removed.
- Live local smoke — five signed duplicate HTTP deliveries produced one accepted inbox row, four duplicate responses, one durable Celery dispatch, one normalized event, and final dispatch state `SUCCEEDED`.
- `docker build --file deploy/docker/api.Dockerfile --tag revenueguard-api:phase2 .` — passed.
- `docker build --file deploy/docker/worker.Dockerfile --tag revenueguard-worker:phase2 .` — passed.
- Secret-pattern scan and `git diff --check` — passed; only the documented placeholder webhook secret matched.

### Phase 3 verification

- `make check` — passed: Ruff format/lint, strict mypy (40 source files), 291 Python tests, 36 contract subtests, frontend Prettier/ESLint/TypeScript, 1 Vitest test, Next.js production build, and Docker Compose validation.
- PostgreSQL integration suite — 13 tests passed with zero skips; includes tenant isolation, row locks, recovery replay/crash behavior, human approval revalidation, deferred resume, and provider authority.
- Disposable migration tests — clean `base -> head` and incremental `0003 -> 0004` passed; `alembic current` and `alembic check` passed; seeded policy digest reconstruction and append-only triggers were verified.
- `docker build --file deploy/docker/worker.Dockerfile --tag revenueguard-worker:local .` — passed.
- Secret-pattern scan and `git diff --check` — passed.

## Known limitations and follow-ups

- Phase 4 owns financial/customer-contact actions, action idempotency, and outcome verification.
- The Phase 2 composition supports one configured Test Mode merchant webhook secret per API instance. Multi-merchant secret-manager lookup, rotation, ingress rate limiting, and retention enforcement remain production-hardening work; the current path fails closed and does not log secrets.
- Phase 3 verification used the existing healthy local PostgreSQL and Redis containers. Disposable schemas/databases were removed; no pre-existing development records or named volumes were deleted.
