# ADR-0001: PostgreSQL Is the Source of Truth

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owners:** RevenueGuard architecture

## Context

RevenueGuard manages money, event uniqueness, state transitions, policy versions, recovery actions, provider outcomes, tenant relationships, and audit history. Queue state, agent checkpoints, caches, and UI state do not provide the transactional constraints required for these records.

## Decision

PostgreSQL is the authoritative financial and workflow store.

It stores merchant policy versions, webhook inbox entries, normalized identities, recovery cases/transitions, incidents, predictions, decision receipts, human reviews, action outbox records/attempts, outcomes, promises, financial metrics, and audit entries.

Redis is limited to queueing, caching, rate limiting, delayed work, and coordination. LangGraph state is workflow context only. Derived dashboard state may always be rebuilt from PostgreSQL.

Schema changes use Alembic. Financial/domain invariants are protected with database uniqueness, foreign keys, checks, and transactional/locking semantics in addition to application validation.

## Consequences

- PostgreSQL availability and backup/restore become critical operational concerns.
- Application services must expose explicit transaction boundaries.
- Redis or worker loss may delay work but cannot erase authoritative history.
- Some queue/agent state is duplicated deliberately for recovery.
- Database migration and concurrency tests are mandatory.

## Verification

- Architecture tests prevent authoritative financial repositories from using Redis/model memory.
- Integration tests prove a worker can reconstruct work after Redis/worker restart.
- Migration tests run from both a clean database and the previous schema.
