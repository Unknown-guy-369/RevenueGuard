# ADR-0004: Idempotent Inbox/Outbox for Exactly-Once Business Effects

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owners:** RevenueGuard architecture

## Context

Exactly-once network delivery cannot be assumed. Events, queue messages, and worker attempts can repeat. A crash can occur between changing case state and calling a provider, or after the provider accepts an action but before the worker records the response.

## Decision

RevenueGuard targets exactly-once logical business effects over at-least-once delivery.

- Provider events are deduplicated with a database unique key.
- Case transitions use optimistic concurrency or row locks.
- Authorized case state and the action outbox record commit in one PostgreSQL transaction.
- Each action has a stable idempotency key derived from merchant, case, action type, target, and logical attempt—not the worker attempt.
- Workers claim outbox rows safely and record every provider attempt.
- An equivalent action is suppressed while one is pending, succeeded, or unknown according to policy.

Provider-native idempotency is used where available but does not replace internal constraints.

## Consequences

- Outbox dispatch and cleanup require operational monitoring.
- Stable business identity must be defined for every action type.
- Duplicate worker work is expected and safe.
- Some read/claim paths may use SQLAlchemy Core or explicit parameterized SQL for locking clarity.

## Verification

- Duplicate webhook, queue redelivery, concurrent worker, and crash-boundary tests create one logical action/effect.
- Database constraints reject duplicate event and action keys.
- Worker retry IDs do not change the business idempotency key.
- A case transition cannot commit without its required outbox intent, or vice versa.
