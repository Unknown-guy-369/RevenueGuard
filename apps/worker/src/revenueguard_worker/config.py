"""Validated worker configuration."""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from revenueguard_agents import StructuredResponseMode, TokenLimitField


class AgentModelProvider(StrEnum):
    DISABLED = "DISABLED"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


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
    action_provider: str = Field(default="SIMULATOR", pattern="^(SIMULATOR|RAZORPAY_TEST)$")
    action_dispatch_batch_size: int = Field(default=50, ge=1, le=1_000)
    action_dispatch_stale_after_seconds: int = Field(default=60, ge=5, le=3_600)
    action_reconciliation_batch_size: int = Field(default=50, ge=1, le=1_000)
    action_unknown_ttl_seconds: int = Field(default=3_600, ge=60, le=604_800)
    agent_model_provider: AgentModelProvider = AgentModelProvider.DISABLED
    agent_model_base_url: str | None = None
    agent_model_name: str | None = None
    agent_model_response_mode: StructuredResponseMode = StructuredResponseMode.JSON_SCHEMA
    agent_model_token_limit_field: TokenLimitField = TokenLimitField.MAX_COMPLETION_TOKENS
    llm_api_key: SecretStr | None = Field(default=None, validation_alias="LLM_API_KEY")
    agent_model_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    agent_workflow_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    agent_model_max_retries: int = Field(default=1, ge=0, le=3)
    agent_model_max_output_tokens: int = Field(default=512, ge=64, le=4_096)
    agent_graph_max_steps: int = Field(default=8, ge=8, le=32)
    razorpay_timeout_seconds: float = Field(default=10, gt=0, le=60)
    razorpay_key_id: SecretStr | None = Field(
        default=None,
        validation_alias="RAZORPAY_KEY_ID",
    )
    razorpay_key_secret: SecretStr | None = Field(
        default=None,
        validation_alias="RAZORPAY_KEY_SECRET",
    )

    @model_validator(mode="after")
    def validate_agent_timeouts(self) -> WorkerSettings:
        if self.agent_workflow_timeout_seconds < self.agent_model_timeout_seconds:
            raise ValueError("agent workflow timeout cannot be shorter than model timeout")
        if self.agent_model_provider is AgentModelProvider.OPENAI_COMPATIBLE:
            if not self.agent_model_base_url or not self.agent_model_base_url.strip():
                raise ValueError("agent model base URL is required for OPENAI_COMPATIBLE")
            if not self.agent_model_name or not self.agent_model_name.strip():
                raise ValueError("agent model name is required for OPENAI_COMPATIBLE")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
