"""Authenticated, merchant-scoped operational dashboard reads."""

from __future__ import annotations

from collections.abc import Awaitable
from hmac import compare_digest
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from revenueguard_api.config import Settings, get_settings
from revenueguard_api.dashboard import (
    CaseDetail,
    CaseList,
    DashboardNotFoundError,
    DashboardOverview,
    DashboardPersistenceError,
    DashboardQueryService,
    OperationsHealth,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]
_CASE_STATES = {
    "DETECTED",
    "DIAGNOSING",
    "DECISION_PENDING",
    "POLICY_CHECK",
    "READY",
    "EXECUTING",
    "VERIFYING",
    "UNKNOWN",
    "DEFERRED",
    "ESCALATED",
    "RECOVERED",
    "STOPPED",
}


async def dashboard_merchant(
    settings: SettingsDependency,
    authorization: Annotated[str | None, Header()] = None,
    merchant_id: Annotated[
        str | None,
        Header(alias="X-RevenueGuard-Merchant-Id"),
    ] = None,
) -> str:
    configured = settings.dashboard_api_token
    if configured is None or not configured.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dashboard API authentication is not configured",
        )
    scheme, separator, credential = (authorization or "").partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not credential
        or not compare_digest(credential, configured.get_secret_value())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="dashboard authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    normalized_merchant = (merchant_id or "").strip()
    if (
        not normalized_merchant
        or len(normalized_merchant) > 128
        or not normalized_merchant.replace("_", "").replace("-", "").isalnum()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="a valid merchant scope is required",
        )
    return normalized_merchant


MerchantDependency = Annotated[str, Depends(dashboard_merchant)]


def _service(request: Request) -> DashboardQueryService:
    return cast(DashboardQueryService, request.app.state.dashboard_query_service)


ServiceDependency = Annotated[DashboardQueryService, Depends(_service)]


@router.get("/overview", response_model=DashboardOverview)
async def overview(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> DashboardOverview:
    return await _map_query(service.overview(merchant_id))


@router.get("/cases", response_model=CaseList)
async def cases(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
    state_filter: Annotated[list[str] | None, Query(alias="state")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CaseList:
    states = tuple(dict.fromkeys(state_filter or ()))
    if any(item not in _CASE_STATES for item in states):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="unsupported recovery case state",
        )
    return await _map_query(service.list_cases(merchant_id, states=states, limit=limit))


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def case_detail(
    case_id: str,
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> CaseDetail:
    if not case_id or len(case_id) > 128 or any(ord(character) < 32 for character in case_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid recovery case reference",
        )
    return await _map_query(service.case_detail(merchant_id, case_id))


@router.get("/health", response_model=OperationsHealth)
async def operations_health(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> OperationsHealth:
    return await _map_query(service.operations_health(merchant_id))


async def _map_query[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except DashboardNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RESOURCE_NOT_FOUND",
                "message": str(error),
            },
        ) from error
    except DashboardPersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authoritative dashboard data is unavailable",
        ) from error
