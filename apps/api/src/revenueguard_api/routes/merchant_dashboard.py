"""Merchant reporting, approval commands, and durable Test Mode simulation routes."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from revenueguard_api.dashboard import DashboardNotFoundError, DashboardPersistenceError
from revenueguard_api.merchant_dashboard import (
    BusinessOverview,
    IncidentList,
    MerchantDashboardConflictError,
    MerchantDashboardService,
    PaymentDetail,
    PaymentList,
    RecoveryOverview,
    RevenueSeries,
    ReviewDecisionRequest,
    ReviewDecisionResult,
    ReviewList,
    SimulationAttemptResult,
    SimulationCreateRequest,
    SimulationEvents,
    SimulationSessionView,
)
from revenueguard_api.routes.dashboard import MerchantDependency

dashboard_router = APIRouter(prefix="/api/v1/dashboard", tags=["merchant-dashboard"])
simulation_router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])
public_simulation_router = APIRouter(
    prefix="/api/v1/public/simulations", tags=["public-simulations"]
)


def _service(request: Request) -> MerchantDashboardService:
    return cast(MerchantDashboardService, request.app.state.merchant_dashboard_service)


ServiceDependency = Annotated[MerchantDashboardService, Depends(_service)]


@dashboard_router.get("/business-overview", response_model=BusinessOverview)
async def business_overview(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> BusinessOverview:
    since = datetime.now(UTC) - timedelta(days=days)
    return await _map(service.business_overview(merchant_id, since=since))


@dashboard_router.get("/revenue-series", response_model=RevenueSeries)
async def revenue_series(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> RevenueSeries:
    since = datetime.now(UTC) - timedelta(days=days)
    return await _map(service.revenue_series(merchant_id, since=since))


@dashboard_router.get("/payments", response_model=PaymentList)
async def payments(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> PaymentList:
    statuses = tuple(dict.fromkeys(item.strip().upper() for item in status_filter or ()))
    if any(not item.replace("_", "").isalnum() for item in statuses):
        raise HTTPException(status_code=422, detail="unsupported payment status")
    return await _map(
        service.payments(
            merchant_id,
            statuses=statuses,
            query=query.strip() if query else None,
            limit=limit,
            offset=offset,
        )
    )


@dashboard_router.get("/payments/{payment_id}", response_model=PaymentDetail)
async def payment_detail(
    payment_id: str,
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> PaymentDetail:
    _validate_reference(payment_id, "payment")
    return await _map(service.payment_detail(merchant_id, payment_id))


@dashboard_router.get("/recovery-overview", response_model=RecoveryOverview)
async def recovery_overview(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> RecoveryOverview:
    return await _map(service.recovery_overview(merchant_id))


@dashboard_router.get("/incidents", response_model=IncidentList)
async def incidents(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
    active_only: bool = True,
) -> IncidentList:
    return await _map(service.incidents(merchant_id, active_only=active_only))


@dashboard_router.get("/reviews", response_model=ReviewList)
async def reviews(
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> ReviewList:
    return await _map(service.reviews(merchant_id))


@dashboard_router.post(
    "/reviews/{review_id}/decision",
    response_model=ReviewDecisionResult,
)
async def decide_review(
    review_id: str,
    decision: ReviewDecisionRequest,
    merchant_id: MerchantDependency,
    service: ServiceDependency,
    operator_id: Annotated[str | None, Header(alias="X-RevenueGuard-Operator-Id")] = None,
) -> ReviewDecisionResult:
    _validate_reference(review_id, "review")
    normalized_operator = (operator_id or "").strip()
    if (
        not normalized_operator
        or len(normalized_operator) > 128
        or not normalized_operator.replace("_", "").replace("-", "").isalnum()
    ):
        raise HTTPException(status_code=400, detail="a valid operator identity is required")
    return await _map(
        service.decide_review(
            merchant_id,
            review_id,
            operator_id=normalized_operator,
            request=decision,
        )
    )


@simulation_router.post("", response_model=SimulationSessionView, status_code=201)
async def create_simulation(
    simulation: SimulationCreateRequest,
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> SimulationSessionView:
    return await _map(service.create_simulation(merchant_id, simulation))


@simulation_router.get("/{simulation_id}/events", response_model=SimulationEvents)
async def simulation_events(
    simulation_id: str,
    merchant_id: MerchantDependency,
    service: ServiceDependency,
) -> SimulationEvents:
    _validate_reference(simulation_id, "simulation")
    return await _map(service.simulation_events(merchant_id, simulation_id))


@public_simulation_router.get("/{simulation_id}", response_model=SimulationSessionView)
async def public_simulation(
    simulation_id: str,
    service: ServiceDependency,
) -> SimulationSessionView:
    _validate_reference(simulation_id, "simulation")
    return await _map(service.simulation(simulation_id))


@public_simulation_router.post("/{simulation_id}/attempt", response_model=SimulationAttemptResult)
async def submit_public_simulation(
    simulation_id: str,
    service: ServiceDependency,
) -> SimulationAttemptResult:
    _validate_reference(simulation_id, "simulation")
    return await _map(service.submit_simulation(simulation_id))


def _validate_reference(value: str, kind: str) -> None:
    if (
        not value
        or len(value) > 128
        or any(ord(character) < 32 for character in value)
        or "/" in value
    ):
        raise HTTPException(status_code=422, detail=f"invalid {kind} reference")


async def _map[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except DashboardNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESOURCE_NOT_FOUND", "message": str(error)},
        ) from error
    except MerchantDashboardConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STATE_CONFLICT", "message": str(error)},
        ) from error
    except DashboardPersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authoritative merchant data is unavailable",
        ) from error
