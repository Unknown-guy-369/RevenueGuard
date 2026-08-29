"""Bounded structured extraction for receivables customer responses."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date
from typing import Protocol

from revenueguard_domain import PromiseExtraction, PromiseIntent


class PromiseExtractionProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    async def extract(self, *, text: str, max_output_tokens: int) -> Mapping[str, object]: ...


class BoundedPromiseExtractor:
    """Validate model output and fall back to UNKNOWN on every model failure."""

    def __init__(
        self,
        provider: PromiseExtractionProvider | None = None,
        *,
        timeout_seconds: float = 3.0,
        max_input_characters: int = 2_000,
        max_output_tokens: int = 200,
    ) -> None:
        if timeout_seconds <= 0 or max_input_characters <= 0 or max_output_tokens <= 0:
            raise ValueError("extraction bounds must be positive")
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._max_input_characters = max_input_characters
        self._max_output_tokens = max_output_tokens

    async def extract(self, text: str) -> PromiseExtraction:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("customer response text is required")
        if len(text) > self._max_input_characters:
            return self._fallback("INPUT_LIMIT_EXCEEDED")
        if self._provider is None:
            return self._fallback("MODEL_UNAVAILABLE")
        try:
            async with asyncio.timeout(self._timeout_seconds):
                payload = await self._provider.extract(
                    text=text,
                    max_output_tokens=self._max_output_tokens,
                )
            return _validated_extraction(payload, self._provider.model_version)
        except Exception:  # Model failures deliberately map to a deterministic safe fallback.
            return self._fallback("MALFORMED_OR_UNAVAILABLE_MODEL")

    @staticmethod
    def _fallback(reason: str) -> PromiseExtraction:
        return PromiseExtraction(
            intent=PromiseIntent.UNKNOWN,
            confidence_basis_points=0,
            extractor_version=f"phase6-deterministic-fallback:{reason}",
        )


def _validated_extraction(payload: Mapping[str, object], model_version: str) -> PromiseExtraction:
    if not isinstance(payload, Mapping):
        raise TypeError("model output must be a mapping")
    allowed = {"intent", "confidence_basis_points", "promised_date", "amount_minor", "currency"}
    if set(payload) - allowed:
        raise ValueError("model output contains unexpected fields")
    intent = PromiseIntent(_required_string(payload, "intent"))
    confidence = payload.get("confidence_basis_points")
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        raise TypeError("confidence_basis_points must be an integer")
    if intent is not PromiseIntent.PROMISE_TO_PAY:
        return PromiseExtraction(
            intent=intent,
            confidence_basis_points=confidence,
            extractor_version=model_version,
        )
    promised_date = date.fromisoformat(_required_string(payload, "promised_date"))
    amount = payload.get("amount_minor")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError("amount_minor must be an integer")
    currency = _required_string(payload, "currency")
    return PromiseExtraction(
        intent=intent,
        confidence_basis_points=confidence,
        extractor_version=model_version,
        promised_date=promised_date,
        amount_minor=amount,
        currency=currency,
    )


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a non-empty string")
    return value
