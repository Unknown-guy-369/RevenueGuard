"""Validated API configuration."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from `REVENUEGUARD_*` variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="REVENUEGUARD_",
        extra="ignore",
    )

    service_name: str = "revenueguard-api"
    environment: str = "development"
    app_version: str = "0.1.0"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    razorpay_webhook_max_body_bytes: int = Field(default=1_048_576, ge=1, le=10_485_760)
    razorpay_merchant_routing_header: str = Field(
        default="X-RevenueGuard-Merchant-Id",
        min_length=1,
        max_length=128,
    )
    razorpay_merchant_id: str | None = Field(default=None, min_length=1, max_length=128)
    razorpay_webhook_secret: SecretStr | None = Field(
        default=None,
        validation_alias="RAZORPAY_WEBHOOK_SECRET",
    )
    event_dispatch_max_attempts: int = Field(default=5, ge=1, le=20)
    dashboard_api_token: SecretStr | None = None
    database_url: str = "postgresql+asyncpg://revenueguard:revenueguard@localhost:5432/revenueguard"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""

    return Settings()
