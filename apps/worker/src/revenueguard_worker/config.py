"""Validated worker configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="REVENUEGUARD_",
        extra="ignore",
    )

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    database_url: str = "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard"
    event_dispatch_batch_size: int = Field(default=100, ge=1, le=1_000)
    event_dispatch_max_attempts: int = Field(default=5, ge=1, le=20)
    event_dispatch_stale_after_seconds: int = Field(default=60, ge=5, le=3_600)


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
