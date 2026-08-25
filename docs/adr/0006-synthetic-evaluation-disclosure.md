# ADR-0006: Separate and Disclose Synthetic Evaluation

- **Status:** Accepted
- **Date:** 2026-08-25
- **Decision owners:** RevenueGuard architecture

## Context

Razorpay Test Mode is appropriate for validating API and webhook integration but does not provide enough representative outcomes to make statistical recovery claims. Synthetic portfolios can evaluate strategies at scale, but simulated results can be misleading when presented as real merchant performance.

## Decision

RevenueGuard uses three explicitly separated evidence tiers:

1. Sanitized Razorpay Test Mode fixtures for provider-contract correctness.
2. Webhook replay for delivery, ordering, crash, and idempotency behavior.
3. Seeded synthetic portfolios with hidden ground truth for comparative strategy evaluation.

All synthetic records and artifacts carry `source = SYNTHETIC`, generator version, configuration hash, and seed. Synthetic reports disclose that results are simulated and are not real merchant recovery rates.

The train/validation/held-out split, baselines, hard safety gates, metrics, and primary success criterion are frozen in `docs/evaluation/SUCCESS_CRITERIA.md` before model/strategy results exist.

## Consequences

- Product claims are narrower but credible and reproducible.
- Evaluation code must preserve seeds, manifests, and machine-readable raw results.
- Test Mode and synthetic performance are shown separately in demos/reports.
- The team must disclose negative results, subgroup weaknesses, and uncertainty.

## Verification

- Validation rejects synthetic fixtures without the required provenance fields.
- Reports contain a simulation disclosure and reproducibility metadata.
- Held-out manifests are immutable inputs to the final evaluation job.
- No dashboard or README labels synthetic recovery as production merchant recovery.
