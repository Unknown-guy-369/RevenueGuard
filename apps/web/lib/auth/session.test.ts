import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/headers", () => ({ cookies: vi.fn() }));

import {
  createDashboardSession,
  verifyDashboardSession,
  verifyOperatorAccessKey,
} from "@/lib/auth/session";

const NOW = new Date("2026-08-27T12:00:00Z");

describe("dashboard operator session", () => {
  beforeEach(() => {
    vi.stubEnv("REVENUEGUARD_DASHBOARD_OPERATOR_ACCESS_KEY", "operator-test-key");
    vi.stubEnv("REVENUEGUARD_DASHBOARD_MERCHANT_ID", "merchant_demo_001");
    vi.stubEnv(
      "REVENUEGUARD_DASHBOARD_SESSION_SECRET",
      "dashboard-session-secret-that-is-at-least-32-characters",
    );
  });

  afterEach(() => vi.unstubAllEnvs());

  it("creates a signed session bound to the configured merchant", () => {
    expect(verifyOperatorAccessKey("operator-test-key")).toBe(true);
    expect(verifyOperatorAccessKey("wrong-key")).toBe(false);
    const value = createDashboardSession(NOW);

    expect(value).not.toBeNull();
    expect(verifyDashboardSession(value!, NOW)?.merchant_id).toBe("merchant_demo_001");
  });

  it("rejects tampering and expiration", () => {
    const value = createDashboardSession(NOW)!;
    expect(verifyDashboardSession(`${value}x`, NOW)).toBeNull();
    expect(verifyDashboardSession(value, new Date("2026-08-28T12:00:00Z"))).toBeNull();
  });
});
