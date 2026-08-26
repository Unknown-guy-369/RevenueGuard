# Database migrations

RevenueGuard uses Alembic for every PostgreSQL schema change.

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic history
```

The current head includes the Phase 2 transactional event inbox, Phase 3 recovery cases and immutable policy history, and Phase 4 action outbox, execution attempts, and verified outcomes. Financial-history migrations intentionally refuse destructive downgrades.
