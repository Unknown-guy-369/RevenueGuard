import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));

import { POST } from "@/app/api/session/route";

function signInRequest(accessKey: string, origin = "http://127.0.0.1:3001") {
  return new Request("http://internal-next-origin:3000/api/session", {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      host: "127.0.0.1:3001",
      origin,
      "sec-fetch-site": origin === "http://127.0.0.1:3001" ? "same-origin" : "cross-site",
    },
    body: new URLSearchParams({ access_key: accessKey }),
  });
}

describe("operator sign-in route", () => {
  beforeEach(() => {
    vi.stubEnv("REVENUEGUARD_DASHBOARD_OPERATOR_ACCESS_KEY", "operator-test-key");
    vi.stubEnv("REVENUEGUARD_DASHBOARD_MERCHANT_ID", "merchant_demo_001");
    vi.stubEnv(
      "REVENUEGUARD_DASHBOARD_SESSION_SECRET",
      "dashboard-session-secret-that-is-at-least-32-characters",
    );
  });

  afterEach(() => vi.unstubAllEnvs());

  it("returns a mutable redirect response carrying a protected session cookie", async () => {
    const response = await POST(signInRequest("operator-test-key"));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("http://127.0.0.1:3001/dashboard");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly; SameSite=Strict");
  });

  it("rejects cross-site and invalid-credential posts", async () => {
    expect(
      await POST(signInRequest("operator-test-key", "https://attacker.example")),
    ).toHaveProperty("status", 403);
    const invalid = await POST(signInRequest("wrong-key"));
    expect(invalid.status).toBe(303);
    expect(invalid.headers.get("location")).toContain("/sign-in?error=invalid");
    expect(invalid.headers.get("set-cookie")).toBeNull();
  });
});
