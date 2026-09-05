# Synthetic batch evaluation runners

The batch runner compares RevenueGuard with the six strategies frozen in
`SUCCESS_CRITERIA.md`. It is an offline simulation: it does not start the API,
connect to PostgreSQL or Redis, call Razorpay, contact a customer, or invoke an
LLM.

It validates the sealed held-out scenario manifest before generating any
result. Scenario expectations remain an independent oracle and are not used by
strategy selection. The strategy comparison uses generated synthetic
portfolios with hidden, deterministic simulator outcomes.

## Run manually

The evaluator never runs automatically. Invoke it explicitly from the
repository root:

```bash
UV_CACHE_DIR=.runtime/uv-cache uv run python -m revenueguard_evaluation.cli \
  --manifest fixtures/evaluation/held_out_v1/manifest.json \
  --output artifacts/evaluation/held_out_v1-report.json \
  --confirm-synthetic
```

The default run uses 10 fixed seeds and 240 cases per seed. Use repeatable
`--seed` options and `--cases-per-seed` only for development checks; submission
results should retain the frozen defaults.

## Report interpretation

The JSON report contains:

- dataset seal, generator version, configuration hash, seeds, and disclosure;
- per-seed and aggregate results for all six frozen strategies;
- verified gross recovery, recovery cost, and verified net recovery in INR
  minor units;
- action, policy, contact, incident-deferment, and unknown-outcome counts;
- mean, standard deviation, and 95% interval for verified net recovery;
- comparison of `REVENUEGUARD_FULL` with the best static retry baseline;
- measured policy, duplicate-effect, verified-money, and limit safety gates;
- explicit `NOT_EVALUATED` results for tenant-query and webhook-loss integration gates;
- explicit limitations and a `NOT_EVALUATED` model-metrics result.

The sealed scenario contract is structurally validated but not executed by
this offline runner. API, queue, database, webhook, and provider correctness
remain separate integration evidence. Never describe this report as a real
merchant result or a Razorpay Test Mode integration result.

## Live integrated batch for the submission demo

The integrated runner is separate from the offline strategy comparison. It
creates eight labelled synthetic sessions through the running FastAPI service,
waits for PostgreSQL/Redis/Celery processing, submits signed synthetic recovery
evidence only after a payment-link action reaches verification, and reads the
case audit back through the authenticated dashboard API.

Prepare `.env` with a non-empty local dashboard token, then start the stack:

```bash
make infra-up
make migrate
make bootstrap-merchant MERCHANT_ID=merchant_demo_001
```

In terminal 1:

```bash
set -a
source .env
set +a
make api
```

In terminal 2:

```bash
set -a
source .env
set +a
make worker
```

In terminal 3, run the batch and save its observed report:

```bash
set -a
source .env
set +a
UV_CACHE_DIR=.runtime/uv-cache uv run python -m revenueguard_evaluation.integrated_cli \
  --manifest fixtures/evaluation/held_out_v1/manifest.json \
  --output artifacts/evaluation/integrated-system-report.json \
  --confirm-synthetic
```

The command exits non-zero on a timed-out flow, expected-state mismatch, policy
violation, unverified recovery credit, action-limit breach, or incomplete audit
evidence. Its recovered amount is synthetic Test Mode evidence only. It must not
be presented as production merchant revenue, and it does not claim that all 29
sealed scenario oracles ran through the live stack.
