# ADR-0005: Authoritative Outcome Verification and Explicit UNKNOWN

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owners:** RevenueGuard architecture

## Context

A provider request can time out after being accepted. An HTTP success can acknowledge request handling without proving final payment recovery. Guessing either success or failure can inflate metrics or create duplicate financial/customer effects.

## Decision

Execution, observation, verification, and recovered-revenue accounting are separate steps.

Outcomes use `PENDING`, `SUCCEEDED`, `FAILED`, or `UNKNOWN`. A timeout or otherwise ambiguous provider result becomes `UNKNOWN`. Equivalent action execution remains blocked until reconciliation.

Authoritative evidence is a supported signed provider webhook or provider state lookup. Simulator evidence is authoritative only inside explicitly synthetic evaluation. A positive recovered amount requires an authoritative `SUCCEEDED` outcome. API acknowledgements may be stored as provisional evidence but do not alone increase recovered revenue unless that provider operation is documented as final and the adapter contract declares it authoritative.

## Consequences

- Recovery metrics may lag execution.
- Reconciliation workers and escalation deadlines are required.
- Operators can see uncertainty rather than a misleading binary result.
- Provider adapter contracts must state authoritative evidence sources.

## Verification

- Provider timeout tests enter `UNKNOWN` with zero recovered amount.
- Equivalent actions are suppressed while unknown.
- Later signed/lookup evidence resolves the outcome correctly.
- Reports and dashboard queries exclude unverified positive amounts.
