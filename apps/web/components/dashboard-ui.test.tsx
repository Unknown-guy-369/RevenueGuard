import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CaseTable } from "@/components/dashboard-ui";

describe("CaseTable", () => {
  it("renders an evidence-linked case without exposing raw provider identifiers", () => {
    render(
      <CaseTable
        cases={[
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
        ]}
      />,
    );

    expect(screen.getByRole("table", { name: "Recovery cases" })).toBeVisible();
    expect(screen.getAllByRole("row")[1]).toHaveAttribute("href", "/dashboard/cases/case_001");
    expect(screen.getByText("Failed subscription")).toBeVisible();
    expect(screen.getByText("SUBSCRIPTION · A1B2C3D4E5")).toBeVisible();
    expect(screen.getByText("₹100.00")).toBeVisible();
  });
});
