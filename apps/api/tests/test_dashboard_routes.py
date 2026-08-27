from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from revenueguard_api.config import Settings, get_settings
from revenueguard_api.dashboard import (
    CaseDetail,
    CaseList,
    CaseSummary,
    CurrencyTotals,
    DashboardContext,
    DashboardCounts,
    DashboardNotFoundError,
    DashboardOverview,
    OperationsHealth,
)
from revenueguard_api.main import create_app

TOKEN = "dashboard-test-token"
MERCHANT_ID = "merchant_demo_001"
NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


def _context() -> DashboardContext:
    return DashboardContext(
        merchant_id=MERCHANT_ID,
        merchant_display_name="Demo merchant",
        as_of=NOW,
    )


def _case() -> CaseSummary:
    return CaseSummary(
        case_id="case_001",
        state="VERIFYING",
        state_version=7,
        workflow_type="FAILED_SUBSCRIPTION",
        subject_type="SUBSCRIPTION",
        subject_reference_masked="SUBSCRIPTION · A1B2C3D4E5",
        customer_reference_masked=None,
        revenue_at_risk_minor=10_000,
        currency="INR",
        diagnosis="EXPIRED_PAYMENT_METHOD",
        diagnosis_confidence_basis_points=9_200,
        retry_count=1,
        contact_count=0,
        updated_at=NOW,
    )


class FakeDashboardService:
    def __init__(self) -> None:
        self.merchant_ids: list[str] = []

    async def overview(self, merchant_id: str) -> DashboardOverview:
        self.merchant_ids.append(merchant_id)
        if merchant_id != MERCHANT_ID:
            raise DashboardNotFoundError("merchant was not found")
        return DashboardOverview(
            context=_context(),
            currency_totals=(
                CurrencyTotals(
                    currency="INR",
                    revenue_at_risk_minor=10_000,
                    verified_recovered_minor=2_500,
                ),
            ),
            counts=DashboardCounts(
                active_cases=1,
                recovered_cases=2,
                stopped_cases=0,
                unknown_cases=0,
                deferred_cases=0,
                escalated_cases=0,
                pending_reviews=0,
                pending_actions=1,
                decision_receipts=3,
                model_succeeded=4,
                model_fallback=0,
            ),
            recent_cases=(_case(),),
        )

    async def list_cases(
        self,
        merchant_id: str,
        *,
        states: tuple[str, ...],
        limit: int,
    ) -> CaseList:
        del states, limit
        self.merchant_ids.append(merchant_id)
        return CaseList(context=_context(), cases=(_case(),), total=1)

    async def case_detail(self, merchant_id: str, case_id: str) -> CaseDetail:
        self.merchant_ids.append(merchant_id)
        if case_id != "case_001":
            raise DashboardNotFoundError("recovery case was not found")
        return CaseDetail(
            context=_context(),
            case=_case(),
            transitions=(),
            decisions=(),
            predictions=(),
            actions=(),
            outcomes=(),
            reviews=(),
        )

    async def operations_health(self, merchant_id: str) -> OperationsHealth:
        self.merchant_ids.append(merchant_id)
        return OperationsHealth(
            context=_context(),
            status="HEALTHY",
            pending_events=0,
            dead_letter_events=0,
            pending_actions=1,
            unknown_actions=0,
        )


def _client(service: FakeDashboardService) -> AsyncClient:
    get_settings.cache_clear()
    app = create_app(dashboard_query_service=service)
    app.dependency_overrides[get_settings] = lambda: Settings(dashboard_api_token=TOKEN)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _headers(merchant_id: str = MERCHANT_ID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-RevenueGuard-Merchant-Id": merchant_id,
    }


async def test_dashboard_overview_is_authenticated_and_tenant_scoped() -> None:
    service = FakeDashboardService()
    async with _client(service) as client:
        response = await client.get("/api/v1/dashboard/overview", headers=_headers())

    assert response.status_code == 200
    assert response.json()["context"]["merchant_id"] == MERCHANT_ID
    assert response.json()["currency_totals"][0]["revenue_at_risk_minor"] == 10_000
    assert service.merchant_ids == [MERCHANT_ID]


async def test_dashboard_rejects_missing_or_wrong_credentials() -> None:
    service = FakeDashboardService()
    async with _client(service) as client:
        missing = await client.get("/api/v1/dashboard/overview")
        wrong = await client.get(
            "/api/v1/dashboard/overview",
            headers={
                "Authorization": "Bearer wrong",
                "X-RevenueGuard-Merchant-Id": MERCHANT_ID,
            },
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert service.merchant_ids == []


async def test_dashboard_never_falls_back_across_merchants() -> None:
    service = FakeDashboardService()
    async with _client(service) as client:
        response = await client.get(
            "/api/v1/dashboard/overview",
            headers=_headers("merchant_other_001"),
        )

    assert response.status_code == 404
    assert service.merchant_ids == ["merchant_other_001"]


async def test_dashboard_case_filters_and_not_found_contract() -> None:
    service = FakeDashboardService()
    async with _client(service) as client:
        listed = await client.get(
            "/api/v1/dashboard/cases?state=VERIFYING&limit=25",
            headers=_headers(),
        )
        invalid = await client.get(
            "/api/v1/dashboard/cases?state=NOT_A_STATE",
            headers=_headers(),
        )
        missing = await client.get(
            "/api/v1/dashboard/cases/case_missing",
            headers=_headers(),
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert invalid.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"


async def test_openapi_declares_dashboard_read_endpoints() -> None:
    service = FakeDashboardService()
    async with _client(service) as client:
        paths = (await client.get("/openapi.json")).json()["paths"]

    assert {
        "/api/v1/dashboard/overview",
        "/api/v1/dashboard/cases",
        "/api/v1/dashboard/cases/{case_id}",
        "/api/v1/dashboard/health",
    }.issubset(paths)
