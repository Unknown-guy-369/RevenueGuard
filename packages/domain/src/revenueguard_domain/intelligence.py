"""Versioned, framework-independent model prediction records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class ModelNode(StrEnum):
    DIAGNOSIS_ASSISTANCE = "DIAGNOSIS_ASSISTANCE"
    STRATEGY_GENERATION = "STRATEGY_GENERATION"
    RANKING = "RANKING"
    EXPLANATION = "EXPLANATION"
    GRAPH = "GRAPH"


class PredictionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelPrediction:
    prediction_id: str
    run_id: str
    case_id: str
    merchant_id: str
    correlation_id: str
    node: ModelNode
    status: PredictionStatus
    input_sha256: str
    output_payload: Mapping[str, object]
    model_version: str
    prompt_version: str
    schema_version: str
    feature_version: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    created_at: datetime
    failure_code: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "prediction_id",
            "run_id",
            "case_id",
            "merchant_id",
            "correlation_id",
            "model_version",
            "prompt_version",
            "schema_version",
            "feature_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{name} must contain 1 to 128 characters")
        if not isinstance(self.node, ModelNode):
            raise TypeError("node must be a ModelNode")
        if not isinstance(self.status, PredictionStatus):
            raise TypeError("status must be a PredictionStatus")
        if _DIGEST_PATTERN.fullmatch(self.input_sha256) is None:
            raise ValueError("input_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.output_payload, Mapping):
            raise TypeError("output_payload must be a mapping")
        for name in ("latency_ms", "input_tokens", "output_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.status is PredictionStatus.SUCCEEDED and self.failure_code is not None:
            raise ValueError("successful predictions cannot have a failure code")
        if self.status is PredictionStatus.FALLBACK and not self.failure_code:
            raise ValueError("fallback predictions require a failure code")
        if self.failure_code is not None and (
            not self.failure_code.isupper() or len(self.failure_code) > 128
        ):
            raise ValueError("failure_code must be an uppercase machine-readable code")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))

    def to_dict(self) -> dict[str, object]:
        return {
            "prediction_id": self.prediction_id,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "correlation_id": self.correlation_id,
            "node": self.node.value,
            "status": self.status.value,
            "input_sha256": self.input_sha256,
            "output_payload": dict(self.output_payload),
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "feature_version": self.feature_version,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "failure_code": self.failure_code,
            "created_at": self.created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
