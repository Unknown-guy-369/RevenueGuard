"""Razorpay raw-body webhook signature verification."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Final

_SHA256_HEX: Final = re.compile(r"^[0-9a-fA-F]{64}$")


def verify_webhook_signature(
    raw_body: bytes,
    signature: str | None,
    webhook_secret: bytes,
) -> bool:
    """Verify an HMAC-SHA256 signature over the exact bytes received.

    Malformed or missing signatures fail closed. The body is deliberately
    accepted only as bytes so callers cannot accidentally verify a parsed and
    reserialized JSON object.
    """

    if not isinstance(raw_body, bytes):
        raise TypeError("raw_body must be bytes")
    if not isinstance(webhook_secret, bytes):
        raise TypeError("webhook_secret must be bytes")
    if not webhook_secret:
        raise ValueError("webhook_secret cannot be empty")
    if signature is None or _SHA256_HEX.fullmatch(signature) is None:
        return False

    expected = hmac.new(webhook_secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())
