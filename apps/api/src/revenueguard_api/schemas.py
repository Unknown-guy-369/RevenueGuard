"""API response contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictResponse(BaseModel):
    """Base response that rejects accidental contract expansion."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictResponse):
    status: Literal["ok"]
    service: str
    version: str


class VersionResponse(StrictResponse):
    service: str
    version: str
    environment: str


class DependencyStatus(StrictResponse):
    postgres: bool
    redis: bool


class ReadinessResponse(StrictResponse):
    status: Literal["ready", "not_ready"]
    service: str
    dependencies: DependencyStatus


class WebhookReceipt(StrictResponse):
    status: Literal["accepted", "duplicate"]
    provider_event_id: str
