# RevenueGuard

RevenueGuard is a bounded, event-driven revenue recovery control plane for Razorpay merchants. It is designed to detect revenue at risk, coordinate safe recovery decisions, apply deterministic merchant policy, execute approved actions idempotently, and count recovered money only after authoritative verification.

> **Current status — Phase 2:** authenticated Razorpay webhook ingestion, the durable event inbox, normalized event persistence, recoverable asynchronous dispatch, and replay tooling are implemented on top of the Phase 1 scaffold. No payment, customer-contact, recovery-action, or recovered-revenue path is enabled.

## Safety model

RevenueGuard deliberately separates recommendation, authorization, execution, and verification:

```text
event → durable state → bounded recommendation → deterministic policy
      → idempotent action → provider verification → verified outcome
```

- PostgreSQL is the authoritative financial and workflow store.
- Redis is used only for queues, caching, rate limits, and coordination.
- An LLM or LangGraph node may recommend an action but cannot execute money movement or customer contact.
- Every future external action must pass deterministic policy and use a durable outbox with a stable idempotency key.
- Ambiguous provider results become `UNKNOWN`; they are never guessed as successful or failed.
- Revenue is counted as recovered only after authoritative provider confirmation.
- Development and demonstrations must use Razorpay Test Mode.

The complete invariants and contributor rules are defined in [AGENTS.md](AGENTS.md).

## Architecture

The target control flow is:

```mermaid
flowchart LR
    Event["Razorpay or merchant event"] --> Gateway["Webhook gateway"]
    Gateway --> Inbox["Idempotent event inbox"]
    Inbox --> Queue["Durable queue"]
    Queue --> Case["Recovery case engine"]
    Case --> Reasoning["Bounded reasoning"]
    Reasoning --> Policy["Deterministic policy"]
    Policy --> Outbox["Action outbox"]
    Outbox --> Executor["Action executor"]
    Executor --> Provider["Provider"]
    Provider --> Verify["Outcome verification"]
    Verify --> Case
```

Phase 2 accepts authenticated Test Mode events into PostgreSQL and normalizes them asynchronously without pretending that later recovery behavior exists.

## Phase 2 capabilities

| Area     | Implemented                                                                                                     |
| -------- | --------------------------------------------------------------------------------------------------------------- |
| API      | Raw-body Razorpay HMAC verification, tenant resolution, durable acceptance/deduplication, and system endpoints   |
| Worker   | Recoverable PostgreSQL-to-Celery dispatch, idempotent normalization, bounded retry, and dead-letter retention    |
| Web      | Responsive Next.js operational dashboard based on the Coinbase design reference                                 |
| Database | Merchant-scoped inbox, normalized events, correlations, provider entities, and dispatch state via Alembic       |
| Domain   | Framework-independent typed `RevenueRiskEvent` and versioned Phase 0 contracts                                  |
| Quality  | Ruff, mypy, pytest, ESLint, TypeScript, Vitest, Prettier, and production build gates                            |
| Delivery | Six-mode webhook replay CLI plus the existing containers, locked dependencies, Make targets, and CI            |
| Safety   | Invalid signatures never normalize; duplicate delivery creates one logical event; external actions stay absent   |

## Technology stack

- **Frontend:** Next.js 16, React 19, TypeScript
- **API:** FastAPI, Pydantic
- **Persistence:** SQLAlchemy, PostgreSQL, Alembic
- **Async work:** Celery, Redis
- **Agent foundation:** LangGraph with bounded, read-only agent authority
- **Observability foundation:** OpenTelemetry
- **Tooling:** uv, npm workspaces, Docker Compose, GitHub Actions

Use the versions pinned in `.python-version`, `.nvmrc`, `uv.lock`, and `package-lock.json`.

## Repository layout

```text
apps/
  api/                    FastAPI service
  worker/                 Celery worker
  web/                    Next.js dashboard
packages/
  domain/                 Framework-independent domain contracts
  agents/                 Bounded agent orchestration boundary
  integrations/           External adapter boundary
  observability/          Logging, metrics, and tracing foundation
  evaluation/             Evaluation tooling boundary
migrations/               Alembic migrations
docs/
  adr/                     Architecture decisions
  contracts/               Versioned contract documentation
  evaluation/              Success criteria and evaluation gates
  product/                 Product scope
deploy/docker/             Production-oriented image definitions
sources/                   Original architecture and design source
tests/                     Cross-package and contract tests
```

## Prerequisites

- Python version specified by `.python-version`
- [uv](https://docs.astral.sh/uv/)
- Node.js version specified by `.nvmrc`
- npm 11
- Docker with Docker Compose
- GNU Make or a compatible `make`

## Quick start

From the repository root:

```bash
make env
make setup
make infra-up
make migrate
make bootstrap-merchant MERCHANT_ID=merchant_demo_001
```

`make env` creates `.env` from `.env.example` only when `.env` does not already exist. Keep real credentials in the ignored `.env` file or a secret manager—never in `.env.example`.

Start each application in a separate terminal:

```bash
make api
```

```bash
make worker
```

```bash
make web
```

### Local endpoints

| Endpoint                           | Purpose                         |
| ---------------------------------- | ------------------------------- |
| `http://localhost:3000`            | RevenueGuard dashboard          |
| `http://localhost:3000/api/health` | Dashboard server health         |
| `http://localhost:8000/docs`       | OpenAPI documentation           |
| `http://localhost:8000/health`     | API process liveness            |
| `http://localhost:8000/ready`      | PostgreSQL and Redis readiness  |
| `http://localhost:8000/version`    | Service version and environment |

The readiness endpoint returns HTTP `503` when PostgreSQL or Redis is unavailable.

## Verification

Run the complete repository gate:

```bash
make check
```

This performs formatting checks, linting, Python and TypeScript type checking, backend and frontend tests, a Next.js production build, and Docker Compose configuration validation.

Focused commands are also available:

```bash
make format-check
make lint
make typecheck
make test
make build
make phase0-test
```

### Runtime smoke tests

With the API, worker, web app, PostgreSQL, and Redis running:

```bash
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
curl --fail http://localhost:8000/version
curl --fail http://localhost:3000/api/health
```

Send a diagnostic task through the real Redis-backed worker:

```bash
uv run python -c "from revenueguard_worker.tasks import ping; print(ping.delay().get(timeout=10))"
```

Expected result:

```text
{'status': 'ok', 'service': 'revenueguard-worker'}
```

Replay sanitized webhook fixtures after configuring a Test Mode merchant and exporting its webhook secret in the current shell:

```bash
export RAZORPAY_WEBHOOK_SECRET='your-test-mode-webhook-secret'
make replay-webhooks MODE=duplicate \
  MERCHANT_ID=merchant_demo_001 \
  FIXTURES='fixtures/razorpay/payment_failed.json'
```

Available modes are `normal`, `duplicate`, `invalid-signature`, `delayed`, `burst`, and `out-of-order`. The CLI derives stable provider event IDs from fixture bytes, signs those exact bytes, and returns a nonzero status when delivery expectations fail.

Dead-letter rows remain visible in PostgreSQL. An operator can requeue one explicitly while preserving replay count, time, and actor:

```bash
make requeue-dead-letter DISPATCH_ID='<uuid>' ACTOR='operator@example'
```

Confirm readiness fails honestly when a required dependency is unavailable:

```bash
docker compose stop redis
curl --include http://localhost:8000/ready
docker compose start redis
```

## Container builds

Build each deployable image from the repository root:

```bash
docker build --file deploy/docker/api.Dockerfile --tag revenueguard-api:local .
docker build --file deploy/docker/worker.Dockerfile --tag revenueguard-worker:local .
docker build --file deploy/docker/web.Dockerfile --tag revenueguard-web:local .
```

Docker Compose currently owns the local PostgreSQL and Redis dependencies. Application services run directly through the Make targets during development.

## Configuration

The checked-in `.env.example` contains local, non-secret defaults. Important variables include:

- `REVENUEGUARD_DATABASE_URL`
- `REVENUEGUARD_ALEMBIC_DATABASE_URL`
- `REVENUEGUARD_REDIS_URL`
- `REVENUEGUARD_CELERY_BROKER_URL`
- `REVENUEGUARD_CELERY_RESULT_BACKEND`
- `REVENUEGUARD_API_URL`

Razorpay, webhook, LLM, and contact-provider credentials must remain untracked. Do not add secrets to variables prefixed with `NEXT_PUBLIC_`, because those values may be exposed to the browser.

## Project documentation

- [Architecture](ARCHITECTURE.md) — implementation-facing components, invariants, and data flow
- [Implementation plan](IMPLEMENTATION_PLAN.md) — phases, exit gates, testing, evaluation, and deployment sequence
- [Agent and contributor rules](AGENTS.md) — mandatory safety, code quality, and verification requirements
- [Design reference](DESIGN.md) — Coinbase-inspired frontend design language
- [Architecture decisions](docs/adr/README.md) — accepted decisions for truth, queues, agent authority, idempotency, verification, and synthetic evaluation
- [Evaluation success criteria](docs/evaluation/SUCCESS_CRITERIA.md) — frozen business and safety gates
- [Original source](sources/RevenueGuard%20%E2%80%94%20Unified%20Agentic%20Revenue%20Recovery%20Control%20Plane.md) — detailed product rationale

## Delivery roadmap

1. **Completed:** product contracts and safety decisions.
2. **Completed:** reproducible repository and service scaffold.
3. **Completed:** transactional webhook ingestion, event inbox, normalization, and core persistence.
4. Recovery case state machine and deterministic merchant policy.
5. Idempotent outbox, provider execution, and outcome reconciliation.
6. Bounded case intelligence and the three core recovery playbooks.
7. Portfolio intelligence, auditability, evaluation, security hardening, and deployment.

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the complete phase-by-phase plan.

## Stop local services

Stop the API, worker, and web processes with `Ctrl+C`, then stop local infrastructure:

```bash
make infra-down
```

Named PostgreSQL and Redis volumes are preserved unless they are explicitly removed.

## Contributing

Read [AGENTS.md](AGENTS.md) before changing code. Every change must preserve the financial and safety invariants, include appropriate tests, and report the exact verification performed. Do not introduce fake success paths, fabricated recovery metrics, tracked secrets, or direct agent authority over external actions.
