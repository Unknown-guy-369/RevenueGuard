import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth/session", () => ({
  getDashboardSession: vi.fn(async () => ({ merchant_id: "merchant_demo_001" })),
}));
vi.mock("@/lib/auth/origin", () => ({ isSameOriginFormPost: vi.fn(() => true) }));

import { proxyApi } from "@/lib/api/proxy";

function request(method = "GET", body?: string) {
  return new Request("http://dashboard.test/api/v1/dashboard/payments?limit=5", {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body,
  });
}

describe("server-side dashboard API proxy", () => {
  beforeEach(() => {
    vi.stubEnv("REVENUEGUARD_API_URL", "http://api.test:8000");
    vi.stubEnv("REVENUEGUARD_DASHBOARD_API_TOKEN", "dashboard-api-token");
    vi.stubEnv("REVENUEGUARD_DASHBOARD_OPERATOR_ID", "operator_001");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("forwards the authenticated merchant and query without exposing credentials", async () => {
    const backend = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("authorization")).toBe("Bearer dashboard-api-token");
      expect(headers.get("x-revenueguard-merchant-id")).toBe("merchant_demo_001");
      return Response.json({ payments: [] });
    });
    vi.stubGlobal("fetch", backend);

    const response = await proxyApi(request(), {
      backendPath: "/api/v1/dashboard/payments",
      authenticated: true,
      includeQuery: true,
    });

    expect(response.status).toBe(200);
    expect(String(backend.mock.calls[0]?.[0])).toBe(
      "http://api.test:8000/api/v1/dashboard/payments?limit=5",
    );
  });

  it("adds operator identity only to authenticated approval mutations", async () => {
    const backend = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("x-revenueguard-operator-id")).toBe("operator_001");
      expect(init?.body).toBe('{"decision":"APPROVE","rationale":"Reviewed evidence"}');
      return Response.json({ case_state: "READY" });
    });
    vi.stubGlobal("fetch", backend);

    const response = await proxyApi(
      request("POST", '{"decision":"APPROVE","rationale":"Reviewed evidence"}'),
      {
        backendPath: "/api/v1/dashboard/reviews/review_001/decision",
        authenticated: true,
        mutation: true,
        requireOperator: true,
      },
    );

    expect(response.status).toBe(200);
    expect(backend).toHaveBeenCalledOnce();
  });

  it("keeps public simulation submissions credential-free", async () => {
    const backend = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.has("authorization")).toBe(false);
      expect(headers.has("x-revenueguard-merchant-id")).toBe(false);
      return Response.json({ classification: "SYNTHETIC" });
    });
    vi.stubGlobal("fetch", backend);

    const response = await proxyApi(request("POST", "{}"), {
      backendPath: "/api/v1/public/simulations/sim_001/attempt",
      authenticated: false,
      mutation: true,
    });

    expect(response.status).toBe(200);
  });

  it("fails explicitly when authoritative API configuration or JSON is unavailable", async () => {
    vi.stubEnv("REVENUEGUARD_API_URL", "");
    const unconfigured = await proxyApi(request(), {
      backendPath: "/api/v1/dashboard/payments",
      authenticated: true,
    });
    vi.stubEnv("REVENUEGUARD_API_URL", "http://api.test:8000");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("bad gateway", { status: 502 })),
    );
    const invalid = await proxyApi(request(), {
      backendPath: "/api/v1/dashboard/payments",
      authenticated: true,
    });

    expect(unconfigured.status).toBe(503);
    expect(invalid.status).toBe(502);
  });
});
