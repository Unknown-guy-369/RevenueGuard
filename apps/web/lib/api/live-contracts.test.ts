import { describe, expect, it } from "vitest";

import { isDashboardOverview } from "@/lib/api/live-contracts";

const overview = {
  context: {
    schema_version: "1.0",
    merchant_id: "merchant_demo_001",
    merchant_display_name: "Demo merchant",
    environment: "TEST",
    data_classification: "TEST",
    as_of: "2026-08-27T12:00:00Z",
  },
  currency_totals: [
    {
      currency: "INR",
      revenue_at_risk_minor: 10_000,
      verified_recovered_minor: 2_500,
    },
  ],
  counts: {
    active_cases: 1,
    recovered_cases: 1,
    stopped_cases: 0,
    unknown_cases: 0,
    deferred_cases: 0,
    escalated_cases: 0,
    pending_reviews: 0,
    pending_actions: 1,
    decision_receipts: 1,
    model_succeeded: 4,
    model_fallback: 0,
  },
  recent_cases: [
    {
      case_id: "case_001",
      state: "VERIFYING",
      state_version: 7,
      workflow_type: "FAILED_SUBSCRIPTION",
      subject_type: "SUBSCRIPTION",
      subject_reference_masked: "SUBSCRIPTION · A1B2C3D4E5",
      customer_reference_masked: null,
      revenue_at_risk_minor: 10_000,
      currency: "INR",
      diagnosis: "EXPIRED_PAYMENT_METHOD",
      diagnosis_confidence_basis_points: 9_200,
      retry_count: 1,
      contact_count: 0,
      classification: "TEST",
      updated_at: "2026-08-27T12:00:00Z",
    },
  ],
};

describe("live dashboard contracts", () => {
  it("accepts an explicit Test Mode, merchant-scoped overview", () => {
    expect(isDashboardOverview(overview)).toBe(true);
  });

  it("rejects unverified financial or cross-environment contract drift", () => {
    expect(
      isDashboardOverview({
        ...overview,
        context: { ...overview.context, environment: "LIVE" },
      }),
    ).toBe(false);
    expect(
      isDashboardOverview({
        ...overview,
        currency_totals: [{ ...overview.currency_totals[0], verified_recovered_minor: -1 }],
      }),
    ).toBe(false);
  });
});
