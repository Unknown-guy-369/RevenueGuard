"""Razorpay webhook gateway."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from revenueguard_api.config import Settings, get_settings
from revenueguard_api.schemas import WebhookReceipt
from revenueguard_api.webhooks import (
    IngestionDisposition,
    InvalidSignatureRecord,
    MerchantWebhookResolver,
    VerifiedRazorpayWebhook,
    WebhookIngestionService,
    WebhookPersistenceError,
    sha256_hex,
    verify_razorpay_signature,
)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def get_merchant_resolver(request: Request) -> MerchantWebhookResolver:
    return cast(MerchantWebhookResolver, request.app.state.merchant_webhook_resolver)


def get_webhook_ingestion_service(request: Request) -> WebhookIngestionService:
    return cast(WebhookIngestionService, request.app.state.webhook_ingestion_service)


MerchantResolverDependency = Annotated[MerchantWebhookResolver, Depends(get_merchant_resolver)]
IngestionServiceDependency = Annotated[
    WebhookIngestionService, Depends(get_webhook_ingestion_service)
]


def _required_header(value: str | None, header_name: str) -> str:
    if value is None or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"missing required header: {header_name}",
        )
    return value.strip()


def _merchant_routing_identifier(
    header_value: str | None,
    *,
    settings: Settings,
) -> str:
    """Resolve an explicit route or the sole configured Test Mode merchant.

    Razorpay does not send RevenueGuard's internal tenant-routing header. The
    fallback is therefore limited to the one merchant explicitly configured at
    the composition root. A supplied blank or incorrect header never falls back.
    """

    if header_value is not None:
        return _required_header(header_value, settings.razorpay_merchant_routing_header)
    if settings.razorpay_merchant_id is not None:
        return settings.razorpay_merchant_id
    return _required_header(None, settings.razorpay_merchant_routing_header)


async def _read_bounded_raw_body(request: Request, maximum_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid Content-Length header",
            ) from error
        if declared_length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid Content-Length header",
            )
        if declared_length > maximum_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="webhook body exceeds configured limit",
            )

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="webhook body exceeds configured limit",
            )
    return bytes(body)


@router.post(
    "/razorpay",
    response_model=WebhookReceipt,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_200_OK: {"model": WebhookReceipt, "description": "Duplicate accepted event"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid webhook signature or merchant"},
        status.HTTP_413_CONTENT_TOO_LARGE: {"description": "Webhook body too large"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Durable inbox unavailable"},
    },
)
async def ingest_razorpay_webhook(
    request: Request,
    response: Response,
    settings: SettingsDependency,
    merchant_resolver: MerchantResolverDependency,
    ingestion_service: IngestionServiceDependency,
    x_razorpay_signature: Annotated[str | None, Header(alias="X-Razorpay-Signature")] = None,
    x_razorpay_event_id: Annotated[str | None, Header(alias="X-Razorpay-Event-Id")] = None,
    x_revenueguard_merchant_id: Annotated[
        str | None, Header(alias="X-RevenueGuard-Merchant-Id")
    ] = None,
) -> WebhookReceipt:
    """Authenticate and durably store one Razorpay event without processing it inline."""

    signature = _required_header(x_razorpay_signature, "X-Razorpay-Signature")
    provider_event_id = _required_header(x_razorpay_event_id, "X-Razorpay-Event-Id")
    configured_routing_value = request.headers.get(settings.razorpay_merchant_routing_header)
    if settings.razorpay_merchant_routing_header.lower() == "x-revenueguard-merchant-id":
        configured_routing_value = x_revenueguard_merchant_id
    routing_identifier = _merchant_routing_identifier(
        configured_routing_value,
        settings=settings,
    )

    raw_body = await _read_bounded_raw_body(
        request,
        settings.razorpay_webhook_max_body_bytes,
    )
    try:
        merchant = await merchant_resolver.resolve(routing_identifier)
    except WebhookPersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="merchant resolution unavailable",
        ) from error
    if merchant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="webhook authentication failed",
        )

    received_at = datetime.now(UTC)
    if not verify_razorpay_signature(raw_body, signature, merchant.webhook_secret):
        record = InvalidSignatureRecord(
            merchant_id=merchant.merchant_id,
            provider_event_id=provider_event_id,
            payload_sha256=sha256_hex(raw_body),
            signature_sha256=sha256_hex(signature.encode("utf-8")),
            received_at=received_at,
        )
        try:
            await ingestion_service.record_invalid_signature(record)
        except WebhookPersistenceError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="webhook audit storage unavailable",
            ) from error
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="webhook authentication failed",
        )

    try:
        decoded_payload = json.loads(raw_body)
    except (JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="signed webhook body must be a JSON object",
        ) from error
    if not isinstance(decoded_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="signed webhook body must be a JSON object",
        )

    webhook = VerifiedRazorpayWebhook(
        merchant_id=merchant.merchant_id,
        provider_event_id=provider_event_id,
        raw_body=raw_body,
        payload=decoded_payload,
        received_at=received_at,
    )
    try:
        disposition = await ingestion_service.ingest_verified(webhook)
    except WebhookPersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="durable webhook inbox unavailable",
        ) from error

    if disposition is IngestionDisposition.DUPLICATE:
        response.status_code = status.HTTP_200_OK
    receipt_status: Literal["accepted", "duplicate"] = (
        "duplicate" if disposition is IngestionDisposition.DUPLICATE else "accepted"
    )
    return WebhookReceipt(status=receipt_status, provider_event_id=provider_event_id)
