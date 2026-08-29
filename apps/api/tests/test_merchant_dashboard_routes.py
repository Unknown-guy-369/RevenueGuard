from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from revenueguard_api.config import Settings, get_settings
from revenueguard_api.dashboard import DashboardContext, UnavailableDashboardQueryService
from revenueguard_api.main import create_app
from revenueguard_api.merchant_dashboard import (
    BusinessCurrencyTotals,
    BusinessOverview,
    ReviewDecisionRequest,
    ReviewDecisionResult,
    SimulationAttemptResult,
    SimulationCreateRequest,
    SimulationSessionView,
)

TOKEN = "merchant-dashboard-test-token"
MERCHANT_ID = "merchant_demo_001"
NOW = datetime(2026, 8, 29, 9, tzinfo=UTC)


def _context() -> DashboardContext:
    return DashboardContext(
        merchant_id=MERCHANT_ID,
        merchant_display_name="Demo merchant",
        as_of=NOW,
    )


def _simulation() -> SimulationSessionView:
    return SimulationSessionView(
        simulation_id="sim_public_001",
        merchant_display_name="Demo merchant",
        scenario="INSUFFICIENT_FUNDS",
        flow_type="ONE_TIME",
        amount_minor=249_900,
        currency="INR",
        status="CREATED",
        classification="SYNTHETIC",
        checkout_path="/demo/checkout/sim_public_001",
        expires_at=NOW + timedelta(hours=1),
    )


class FakeMerchantDashboardService:
    def __init__(self) -> None:
        self.merchant_ids: list[str] = []
        self.decisions: list[tuple[str, str, str, ReviewDecisionRequest]] = []

    async def business_overview(self, merchant_id: str, *, since: datetime) -> BusinessOverview:
        self.merchant_ids.append(merchant_id)
        return BusinessOverview(
            context=_context(),
            since=since,
            currency_totals=(
                BusinessCurrencyTotals(
                    currency="INR",
                    gross_volume_minor=100_000,
                    collected_minor=75_000,
                    failed_value_minor=25_000,
                    verified_recovered_minor=5_000,
                    payment_count=4,
                    successful_payment_count=3,
                    failed_payment_count=1,
                    success_rate_basis_points=7_500,
                ),
            ),
            payment_methods=(),
        )

    async def decide_review(
        self,
        merchant_id: str,
        review_id: str,
        *,
        operator_id: str,
        request: ReviewDecisionRequest,
    ) -> ReviewDecisionResult:
        self.decisions.append((merchant_id, review_id, operator_id, request))
        return ReviewDecisionResult(
            review_id=review_id,
            case_id="case_001",
            case_state="READY",
            reason_code="HUMAN_REVIEW_APPROVED",
        )

    async def create_simulation(
        self, merchant_id: str, request: SimulationCreateRequest
    ) -> SimulationSessionView:
        del request
        self.merchant_ids.append(merchant_id)
        return _simulation()

    async def simulation(self, simulation_id: str) -> SimulationSessionView:
        assert simulation_id == "sim_public_001"
        return _simulation()

    async def submit_simulation(self, simulation_id: str) -> SimulationAttemptResult:
        return SimulationAttemptResult(
            simulation_id=simulation_id,
            status="SUBMITTED",
            classification="SYNTHETIC",
            provider_event_id="sim_evt_001",
        )


def _client(service: FakeMerchantDashboardService) -> AsyncClient:
    get_settings.cache_clear()
    app = create_app(
        dashboard_query_service=UnavailableDashboardQueryService(),
        merchant_dashboard_service=service,
    )
    app.dependency_overrides[get_settings] = lambda: Settings(dashboard_api_token=TOKEN)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-RevenueGuard-Merchant-Id": MERCHANT_ID,
    }


async def test_business_overview_requires_auth_and_uses_authenticated_merchant() -> None:
    service = FakeMerchantDashboardService()
    async with _client(service) as client:
        missing = await client.get("/api/v1/dashboard/business-overview")
        response = await client.get(
            "/api/v1/dashboard/business-overview?days=7", headers=_headers()
        )

    assert missing.status_code == 401
    assert response.status_code == 200
    assert response.json()["context"]["merchant_id"] == MERCHANT_ID
    assert response.json()["currency_totals"][0]["gross_volume_minor"] == 100_000
    assert service.merchant_ids == [MERCHANT_ID]


async def test_review_decision_requires_operator_and_preserves_typed_command() -> None:
    service = FakeMerchantDashboardService()
    request = {"decision": "APPROVE", "rationale": "Evidence supports one bounded retry."}
    async with _client(service) as client:
        missing_operator = await client.post(
            "/api/v1/dashboard/reviews/review_001/decision",
            headers=_headers(),
            json=request,
        )
        approved = await client.post(
            "/api/v1/dashboard/reviews/review_001/decision",
            headers={**_headers(), "X-RevenueGuard-Operator-Id": "operator_001"},
            json=request,
        )

    assert missing_operator.status_code == 400
    assert approved.status_code == 200
    assert approved.json()["case_state"] == "READY"
    assert len(service.decisions) == 1
    merchant_id, review_id, operator_id, command = service.decisions[0]
    assert (merchant_id, review_id, operator_id) == (
        MERCHANT_ID,
        "review_001",
        "operator_001",
    )
    assert command.decision == "APPROVE"


async def test_public_checkout_lookup_and_attempt_do_not_require_dashboard_token() -> None:
    service = FakeMerchantDashboardService()
    async with _client(service) as client:
        lookup = await client.get("/api/v1/public/simulations/sim_public_001")
        attempt = await client.post("/api/v1/public/simulations/sim_public_001/attempt")

    assert lookup.status_code == 200
    assert lookup.json()["classification"] == "SYNTHETIC"
    assert attempt.status_code == 200
    assert attempt.json()["provider_event_id"] == "sim_evt_001"


async def test_openapi_declares_merchant_dashboard_and_simulator_routes() -> None:
    async with _client(FakeMerchantDashboardService()) as client:
        paths = (await client.get("/openapi.json")).json()["paths"]

    assert {
        "/api/v1/dashboard/business-overview",
        "/api/v1/dashboard/revenue-series",
        "/api/v1/dashboard/payments",
        "/api/v1/dashboard/payments/{payment_id}",
        "/api/v1/dashboard/recovery-overview",
        "/api/v1/dashboard/incidents",
        "/api/v1/dashboard/reviews",
        "/api/v1/dashboard/reviews/{review_id}/decision",
        "/api/v1/simulations",
        "/api/v1/simulations/{simulation_id}/events",
        "/api/v1/public/simulations/{simulation_id}",
        "/api/v1/public/simulations/{simulation_id}/attempt",
    }.issubset(paths)
