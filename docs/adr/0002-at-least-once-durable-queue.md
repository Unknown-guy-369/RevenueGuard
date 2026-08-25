# ADR-0002: At-Least-Once Durable Asynchronous Delivery

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owners:** RevenueGuard architecture

## Context

Razorpay webhooks and ordinary queue systems may deliver messages more than once, late, or out of order. Performing full recovery processing inside the webhook request increases provider retries, latency, and data-loss risk during partial failure.

## Decision

The webhook gateway verifies and durably stores a valid event in an idempotent PostgreSQL inbox, then acknowledges it. Processing is asynchronous through an at-least-once durable queue.

Consumers must tolerate duplicates, reordering, process crashes, and poison messages. Failed work uses bounded retries and a dead-letter path with observable replay tooling. Delayed recovery uses durable schedule state, not process-local timers.

The prototype uses Celery and Redis; the production mapping is an SQS-style durable queue. Queue choice does not change the at-least-once consumer contract.

## Consequences

- Every consumer must be idempotent.
- Business ordering is resolved using provider time, authoritative object state, and case concurrency control rather than queue order alone.
- The API can return quickly after durable acceptance.
- Backpressure, per-merchant fairness, event age, and dead letters require monitoring.

## Verification

- Replay the same event five times and assert one logical processing result.
- Inject out-of-order events and assert no terminal-state regression.
- Crash after inbox commit and before queue acknowledgement; assert eventual processing.
- Send a poison event and assert it is retained and observable in the dead-letter path.
