# ADR-0003: Bounded Agent Authority

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owners:** RevenueGuard architecture

## Context

LLMs are useful for ambiguous diagnosis, intent extraction, strategy generation, explanations, and communication drafting. They are probabilistic and can produce malformed, unsupported, or fabricated output. Financial execution and consent enforcement require deterministic behavior.

## Decision

LangGraph coordinates bounded reasoning using typed state, schema-validated outputs, read-only tools, time/token/step limits, and deterministic fallbacks.

Agents may read committed evidence and recommend a typed action. They may not authorize or execute Razorpay/customer-contact actions, alter money, mark outcomes, update recovered revenue, bypass policy, or mutate authoritative financial records.

Every recommendation goes to the deterministic policy engine. Only a policy-authorized outbox record can reach an executor. Model, prompt, schema, feature, and application versions are stored with each decision receipt.

## Consequences

- Agent flexibility is intentionally constrained.
- Some orchestration remains ordinary application code rather than agent personas.
- Model failure can reduce personalization/optimization but cannot remove safety controls.
- The system can explain where probabilistic reasoning ended and deterministic authority began.

## Verification

- Agent tool registries contain no external money/contact mutation tools.
- Malformed output, timeout, unavailable model, and hallucinated action tests fail safely.
- A recommendation cannot create an action without a deterministic `PROCEED` policy result.
- Contract tests prove decision receipts capture all required versions.
