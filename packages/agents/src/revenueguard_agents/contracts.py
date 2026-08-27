"""Typed contracts for bounded, read-only case intelligence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from revenueguard_domain import (
    ActionType,
    CandidateAction,
    ContactChannel,
    ModelPrediction,
    SubjectType,
    WorkflowType,
)

AGENT_SCHEMA_VERSION = "phase5-agent-schema-1.0"
DEFAULT_PROMPT_VERSION = "phase5-case-intelligence-1.0"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceItem(_StrictModel):
    """Minimal evidence safe for a model; references stay outside model payloads."""

    reference: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    failure_category: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=1_000)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)


class SanitizedCaseContext(_StrictModel):
    """No merchant, customer, provider object, contact, or payment identifiers."""

    workflow_type: WorkflowType
    subject_type: SubjectType
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    diagnosis_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    diagnosis_confidence_basis_points: int = Field(ge=0, le=10_000)
    retry_count: int = Field(ge=0)
    contact_count: int = Field(ge=0)
    supported_action_types: tuple[ActionType, ...]


class DiagnosisOutput(_StrictModel):
    diagnosis_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    confidence_basis_points: int = Field(ge=0, le=10_000)
    rationale: str = Field(min_length=1, max_length=1_000)


class StrategyOutputItem(_StrictModel):
    action_type: ActionType
    recovery_probability_basis_points: int = Field(ge=0, le=10_000)
    expected_net_recovery_minor: int
    channel: ContactChannel | None = None

    @model_validator(mode="after")
    def _channel_matches_action(self) -> StrategyOutputItem:
        contact_actions = {
            ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
            ActionType.SEND_REMINDER,
            ActionType.SCHEDULE_PROMISE_REMINDER,
        }
        if self.action_type in contact_actions and self.channel is None:
            raise ValueError("customer-contact strategies require a channel")
        if self.action_type not in contact_actions and self.channel is not None:
            raise ValueError("only customer-contact strategies accept a channel")
        return self


class StrategyOutput(_StrictModel):
    strategies: tuple[StrategyOutputItem, ...] = Field(min_length=1, max_length=9)

    @field_validator("strategies")
    @classmethod
    def _unique_actions(
        cls, value: tuple[StrategyOutputItem, ...]
    ) -> tuple[StrategyOutputItem, ...]:
        actions = [item.action_type for item in value]
        if len(set(actions)) != len(actions):
            raise ValueError("strategy action types must be unique")
        return value


class RankingOutput(_StrictModel):
    ordered_action_types: tuple[ActionType, ...] = Field(min_length=1, max_length=10)

    @field_validator("ordered_action_types")
    @classmethod
    def _unique_actions(cls, value: tuple[ActionType, ...]) -> tuple[ActionType, ...]:
        if len(set(value)) != len(value):
            raise ValueError("ranked action types must be unique")
        return value


class ExplanationOutput(_StrictModel):
    explanation: str = Field(min_length=1, max_length=2_000)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelResponse:
    payload: Mapping[str, object]
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("model payload must be a mapping")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


class StructuredModel(Protocol):
    """Provider-neutral model boundary; it has no action or persistence methods."""

    @property
    def model_version(self) -> str: ...

    async def generate(
        self,
        *,
        node: str,
        payload: Mapping[str, object],
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> ModelResponse: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseIntelligenceRequest:
    run_id: str
    case_id: str
    merchant_id: str
    correlation_id: str
    workflow_type: WorkflowType
    subject_type: SubjectType
    target: str
    amount_minor: int
    currency: str
    diagnosis_code: str
    diagnosis_confidence_basis_points: int
    candidates: tuple[CandidateAction, ...]
    retry_count: int
    contact_count: int
    evidence: tuple[EvidenceItem, ...]
    feature_version: str
    evaluated_at: datetime
    terminal_diagnosis: bool = False

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "case_id",
            "merchant_id",
            "correlation_id",
            "target",
            "currency",
            "diagnosis_code",
            "feature_version",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        if self.amount_minor < 0 or self.retry_count < 0 or self.contact_count < 0:
            raise ValueError("amount and counters cannot be negative")
        if not 0 <= self.diagnosis_confidence_basis_points <= 10_000:
            raise ValueError("diagnosis confidence must be within basis-point bounds")
        if not self.candidates or self.candidates[-1].action_type is not ActionType.NO_ACTION:
            raise ValueError("candidate list must end with NO_ACTION")
        if not self.evidence:
            raise ValueError("case intelligence requires evidence")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(UTC))


@dataclass(frozen=True, slots=True, kw_only=True)
class CaseIntelligenceResult:
    diagnosis_code: str
    confidence_basis_points: int
    candidates: tuple[CandidateAction, ...]
    explanation: str
    predictions: tuple[ModelPrediction, ...]
    model_version: str
    prompt_version: str
    schema_version: str
    feature_version: str
    fallback_used: bool

    def __post_init__(self) -> None:
        if not self.diagnosis_code or not self.explanation:
            raise ValueError("diagnosis and explanation are required")
        if not 0 <= self.confidence_basis_points <= 10_000:
            raise ValueError("confidence must be within basis-point bounds")
        if not self.candidates or self.candidates[-1].action_type is not ActionType.NO_ACTION:
            raise ValueError("recommendation candidates must end with NO_ACTION")
        if not self.predictions:
            raise ValueError("recommendation requires traceable predictions")
        for name in ("model_version", "prompt_version", "schema_version", "feature_version"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")


class CaseIntelligence(Protocol):
    async def recommend(self, request: CaseIntelligenceRequest) -> CaseIntelligenceResult: ...


class ReadOnlyCaseTools(Protocol):
    """The complete tool surface available to reasoning nodes."""

    async def load_context(self) -> SanitizedCaseContext: ...

    async def load_evidence(self) -> tuple[EvidenceItem, ...]: ...
