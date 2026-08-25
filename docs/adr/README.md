# Architecture Decision Records

ADRs capture decisions that constrain RevenueGuard implementation. Accepted ADRs are normative unless superseded by a later ADR.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-postgresql-source-of-truth.md) | PostgreSQL is the financial and workflow source of truth | Accepted |
| [0002](0002-at-least-once-durable-queue.md) | Use at-least-once asynchronous delivery with durable recovery | Accepted |
| [0003](0003-bounded-agent-authority.md) | Agents recommend; deterministic policy and services authorize/execute | Accepted |
| [0004](0004-idempotent-outbox-business-effects.md) | Use inbox/outbox and stable idempotency for exactly-once business effects | Accepted |
| [0005](0005-authoritative-outcome-verification.md) | Verify provider outcomes and preserve explicit `UNKNOWN` | Accepted |
| [0006](0006-synthetic-evaluation-disclosure.md) | Separate integration evidence from labelled synthetic performance evidence | Accepted |

## ADR format

Each ADR includes status, date, context, decision, consequences, and verification. Superseding an ADR requires a new record that links both decisions; do not rewrite accepted history to hide a change.
