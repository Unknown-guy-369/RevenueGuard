"""Deterministic execution adapters with explicit ambiguous-result handling."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from revenueguard_domain import ActionStatus, ActionType, EvidenceSource, RecoveryAction


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderExecutionResult:
    status: ActionStatus
    evidence_source: EvidenceSource
    observed_at: datetime
    response_category: str
    provider_object_id: str | None = None
    response_reference: str | None = None
    provider_status_code: int | None = None
    error_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.status not in {
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
            ActionStatus.UNKNOWN,
        }:
            raise ValueError("provider execution must be succeeded, failed, or unknown")
        if self.status is ActionStatus.UNKNOWN and self.retryable:
            raise ValueError("ambiguous results must reconcile instead of retrying")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderLookupResult:
    status: ActionStatus
    evidence_source: EvidenceSource
    evidence_reference: str
    observed_at: datetime
    is_authoritative: bool
    provider_object_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.evidence_source not in {EvidenceSource.PROVIDER_LOOKUP, EvidenceSource.SIMULATOR}:
            raise ValueError("lookup evidence must come from lookup or simulator")
        if self.status is ActionStatus.UNKNOWN and self.is_authoritative:
            raise ValueError("UNKNOWN lookup results cannot be authoritative")


class ActionProvider(Protocol):
    async def execute(self, action: RecoveryAction) -> ProviderExecutionResult: ...

    async def lookup(self, action: RecoveryAction) -> ProviderLookupResult: ...


class DeterministicSimulatorAdapter:
    """Explicitly synthetic adapter; outcomes derive only from persisted parameters."""

    async def execute(self, action: RecoveryAction) -> ProviderExecutionResult:
        outcome = str(action.parameters.get("simulation_outcome", "ACCEPTED")).upper()
        provider_object_id = f"sim_{sha256(action.idempotency_key.encode()).hexdigest()[:24]}"
        now = datetime.now(UTC)
        if outcome in {"TIMEOUT", "AMBIGUOUS"}:
            return ProviderExecutionResult(
                status=ActionStatus.UNKNOWN,
                evidence_source=EvidenceSource.SIMULATOR,
                observed_at=now,
                response_category="SIMULATED_AMBIGUOUS",
                provider_object_id=provider_object_id,
                error_code="SIMULATED_TIMEOUT",
            )
        if outcome in {"RETRYABLE_FAILURE", "REJECTED"}:
            return ProviderExecutionResult(
                status=ActionStatus.FAILED,
                evidence_source=EvidenceSource.SIMULATOR,
                observed_at=now,
                response_category="SIMULATED_REJECTION",
                error_code="SIMULATED_RETRYABLE"
                if outcome == "RETRYABLE_FAILURE"
                else "SIMULATED_REJECTED",
                retryable=outcome == "RETRYABLE_FAILURE",
            )
        return ProviderExecutionResult(
            status=ActionStatus.SUCCEEDED,
            evidence_source=EvidenceSource.SIMULATOR,
            observed_at=now,
            response_category="SIMULATED_ACCEPTED",
            provider_object_id=provider_object_id,
            response_reference=f"simulator/{provider_object_id}",
        )

    async def lookup(self, action: RecoveryAction) -> ProviderLookupResult:
        outcome = str(action.parameters.get("simulation_lookup_outcome", "PENDING")).upper()
        provider_object_id = action.parameters.get("simulation_provider_object_id")
        if not isinstance(provider_object_id, str):
            provider_object_id = f"sim_{sha256(action.idempotency_key.encode()).hexdigest()[:24]}"
        status = {
            "SUCCEEDED": ActionStatus.SUCCEEDED,
            "FAILED": ActionStatus.FAILED,
            "UNKNOWN": ActionStatus.UNKNOWN,
        }.get(outcome, ActionStatus.PENDING)
        return ProviderLookupResult(
            status=status,
            evidence_source=EvidenceSource.SIMULATOR,
            evidence_reference=f"simulator/{provider_object_id}/{outcome.lower()}",
            observed_at=datetime.now(UTC),
            # Simulator evidence is never financial authority in an ordinary merchant workflow.
            is_authoritative=False,
            provider_object_id=provider_object_id,
            reason_code=f"SIMULATED_{outcome}",
        )


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes
    request_reference: str


class HttpTransport(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        return await asyncio.to_thread(
            self._request,
            method=method,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _request(
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=response.read(),
                    request_reference=url,
                )
        except HTTPError as error:
            return HttpResponse(
                status_code=error.code,
                body=error.read(),
                request_reference=url,
            )
        except (TimeoutError, URLError) as error:
            raise TimeoutError("Razorpay transport result is ambiguous") from error


class RazorpayTestModeAdapter:
    """Razorpay payment-link adapter that refuses non-Test-Mode credentials."""

    def __init__(
        self,
        *,
        key_id: str,
        key_secret: str,
        transport: HttpTransport | None = None,
        timeout_seconds: float = 10,
        base_url: str = "https://api.razorpay.com/v1",
    ) -> None:
        if not key_id.startswith("rzp_test_"):
            raise ValueError("Razorpay adapter requires an rzp_test_ key")
        if not key_secret:
            raise ValueError("Razorpay key secret is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        self._transport = transport or UrllibHttpTransport()
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url.rstrip("/")

    async def execute(self, action: RecoveryAction) -> ProviderExecutionResult:
        if action.action_type is not ActionType.CREATE_PAYMENT_LINK:
            return ProviderExecutionResult(
                status=ActionStatus.FAILED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=datetime.now(UTC),
                response_category="UNSUPPORTED_ACTION",
                error_code="RAZORPAY_ACTION_UNSUPPORTED",
            )
        payload = {
            "amount": action.parameters["amount_minor"],
            "currency": action.parameters["currency"],
            "description": "RevenueGuard Test Mode recovery",
            "reference_id": _reference_id(action),
            "notes": {"revenueguard_idempotency_key": action.idempotency_key},
        }
        try:
            response = await self._transport.request(
                method="POST",
                url=f"{self._base_url}/payment_links",
                headers=self._headers,
                body=json.dumps(payload, separators=(",", ":")).encode(),
                timeout_seconds=self._timeout_seconds,
            )
        except TimeoutError:
            return ProviderExecutionResult(
                status=ActionStatus.UNKNOWN,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=datetime.now(UTC),
                response_category="TRANSPORT_TIMEOUT",
                error_code="PROVIDER_TIMEOUT",
            )
        document = _json_document(response.body)
        raw_provider_id = document.get("id")
        provider_id = raw_provider_id if isinstance(raw_provider_id, str) else None
        payment_link_url = _razorpay_test_payment_link_url(document)
        if 200 <= response.status_code < 300 and provider_id is not None:
            return ProviderExecutionResult(
                status=ActionStatus.SUCCEEDED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=datetime.now(UTC),
                response_category="API_ACCEPTED",
                provider_object_id=provider_id,
                provider_status_code=response.status_code,
                response_reference=payment_link_url,
            )
        if response.status_code == 429:
            return ProviderExecutionResult(
                status=ActionStatus.FAILED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=datetime.now(UTC),
                response_category="RATE_LIMITED",
                provider_status_code=response.status_code,
                error_code="PROVIDER_RATE_LIMITED",
                retryable=True,
            )
        if 400 <= response.status_code < 500:
            return ProviderExecutionResult(
                status=ActionStatus.FAILED,
                evidence_source=EvidenceSource.PROVIDER_RESPONSE,
                observed_at=datetime.now(UTC),
                response_category="API_REJECTED",
                provider_status_code=response.status_code,
                error_code="PROVIDER_REJECTED",
            )
        return ProviderExecutionResult(
            status=ActionStatus.UNKNOWN,
            evidence_source=EvidenceSource.PROVIDER_RESPONSE,
            observed_at=datetime.now(UTC),
            response_category="AMBIGUOUS_PROVIDER_RESPONSE",
            provider_status_code=response.status_code,
            error_code="PROVIDER_RESPONSE_AMBIGUOUS",
        )

    async def lookup(self, action: RecoveryAction) -> ProviderLookupResult:
        provider_object = action.parameters.get("provider_object_id")
        provider_id = str(provider_object) if provider_object else None
        reference_id = _reference_id(action)
        reference_query = urlencode({"reference_id": reference_id})
        url = (
            f"{self._base_url}/payment_links/{provider_id}"
            if provider_id is not None
            else f"{self._base_url}/payment_links/?{reference_query}"
        )
        try:
            response = await self._transport.request(
                method="GET",
                url=url,
                headers=self._headers,
                body=None,
                timeout_seconds=self._timeout_seconds,
            )
        except TimeoutError:
            return ProviderLookupResult(
                status=ActionStatus.UNKNOWN,
                evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                evidence_reference=(
                    f"razorpay/payment_links/{provider_id or reference_id}/timeout"
                ),
                observed_at=datetime.now(UTC),
                is_authoritative=False,
                provider_object_id=provider_id,
                reason_code="PROVIDER_LOOKUP_TIMEOUT",
            )
        document = _json_document(response.body)
        if provider_id is None:
            links = document.get("payment_links")
            matching = (
                [
                    item
                    for item in links
                    if isinstance(item, dict) and item.get("reference_id") == _reference_id(action)
                ]
                if isinstance(links, list)
                else []
            )
            if len(matching) != 1:
                return ProviderLookupResult(
                    status=ActionStatus.UNKNOWN,
                    evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                    evidence_reference=response.request_reference,
                    observed_at=datetime.now(UTC),
                    is_authoritative=False,
                    reason_code="PROVIDER_REFERENCE_NOT_RESOLVED",
                )
            document = matching[0]
            raw_provider_id = document.get("id")
            provider_id = raw_provider_id if isinstance(raw_provider_id, str) else None
            if provider_id is None:
                return ProviderLookupResult(
                    status=ActionStatus.UNKNOWN,
                    evidence_source=EvidenceSource.PROVIDER_LOOKUP,
                    evidence_reference=response.request_reference,
                    observed_at=datetime.now(UTC),
                    is_authoritative=False,
                    reason_code="PROVIDER_LOOKUP_MALFORMED",
                )
        state = str(document.get("status", "")).lower()
        status = {
            "paid": ActionStatus.SUCCEEDED,
            "cancelled": ActionStatus.FAILED,
            "expired": ActionStatus.FAILED,
            "created": ActionStatus.PENDING,
            "partially_paid": ActionStatus.PENDING,
        }.get(state, ActionStatus.UNKNOWN)
        return ProviderLookupResult(
            status=status,
            evidence_source=EvidenceSource.PROVIDER_LOOKUP,
            evidence_reference=response.request_reference,
            observed_at=datetime.now(UTC),
            is_authoritative=200 <= response.status_code < 300
            and status is not ActionStatus.UNKNOWN,
            provider_object_id=provider_id,
            reason_code=f"PAYMENT_LINK_{state.upper()}"
            if state
            else "PROVIDER_LOOKUP_UNRECOGNIZED",
        )


def _json_document(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(body)
    except UnicodeDecodeError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _razorpay_test_payment_link_url(document: Mapping[str, object]) -> str | None:
    """Return only a Razorpay-hosted HTTPS short URL safe for an operator link."""

    value = document.get("short_url")
    if not isinstance(value, str) or len(value) > 512:
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.path
        or host not in {"rzp.io", "www.rzp.io"}
        or port not in {None, 443}
    ):
        return None
    return value


def _reference_id(action: RecoveryAction) -> str:
    return action.idempotency_key[-40:]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
