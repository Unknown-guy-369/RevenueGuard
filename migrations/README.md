# Database migrations

RevenueGuard uses Alembic for every PostgreSQL schema change.

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic history
```

Phase 1 contains an empty baseline revision that proves migration wiring. Phase 2 will introduce the transactional event inbox and initial domain tables.
