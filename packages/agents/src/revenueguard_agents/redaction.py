"""Data minimization and defensive redaction for model-bound payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Final

_SENSITIVE_KEYS: Final = frozenset(
    {
        "authorization",
        "card",
        "card_number",
        "customer_id",
        "email",
        "merchant_id",
        "password",
        "payment_id",
        "phone",
        "raw_body",
        "secret",
        "subject_id",
        "token",
    }
)
_EMAIL: Final = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE: Final = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
_CREDENTIAL: Final = re.compile(
    r"\b(?:rzp_(?:test|live)_[A-Za-z0-9]+|sk-[A-Za-z0-9_-]{12,})\b",
    re.IGNORECASE,
)


def redact_model_payload(value: object, *, key: str | None = None) -> object:
    """Return a JSON-compatible copy with sensitive keys and common PII removed."""

    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        redacted = _EMAIL.sub("[REDACTED_EMAIL]", value)
        redacted = _PHONE.sub("[REDACTED_PHONE]", redacted)
        return _CREDENTIAL.sub("[REDACTED_CREDENTIAL]", redacted)
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_model_payload(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_model_payload(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)
