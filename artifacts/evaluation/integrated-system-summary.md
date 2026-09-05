# RevenueGuard integrated system result

> **SYNTHETIC TEST MODE RESULT — NOT PRODUCTION MERCHANT REVENUE**

Run window: 5 September 2026, 19:44:50–19:47:06 IST  
Result: **PASS**

## Measured batch result

| Measure | Observed result |
|---|---:|
| Synthetic cases | 8 |
| Revenue at risk | ₹2,017.00 |
| Authoritatively verified gross recovery | ₹757.00 |
| Recovery rate | 37.53% |
| Recovered cases | 3 |
| Human-review escalations | 1 |
| Safe deferrals | 3 |
| Policy violations | 0 |
| Unverified amount counted | ₹0.00 |
| Actions beyond configured limits | 0 |
| Recovered cases missing audit evidence | 0 |
| Duplicate replay returned the same provider event ID | Yes |

## Integrated boundaries observed

The runner created sessions through the authenticated FastAPI service, stored signed synthetic
webhooks in the durable PostgreSQL inbox, waited for Redis/Celery dispatch, normalized failures,
created recovery cases, ran bounded case intelligence, applied deterministic policy, executed
authorized outbox actions, verified signed success evidence, and read the resulting audit trail
back through PostgreSQL-backed dashboard APIs.

The three recovered authentication-failure cases recorded one decision, one action, two outcome
observations, and seven state transitions each. The ₹750.00 high-value case created no action and
was escalated with `REQUIRE_HUMAN`. Insufficient funds, issuer outage, and timeout cases created no
action and were safely deferred.

## Scope limit

This is a representative eight-case live integration batch. It is linked to the sealed
`held_out_v1` manifest, but it does not claim that all 29 sealed scenario oracles were executed
through the live stack. The complete machine-readable evidence is in
`artifacts/evaluation/integrated-system-report.json`.
