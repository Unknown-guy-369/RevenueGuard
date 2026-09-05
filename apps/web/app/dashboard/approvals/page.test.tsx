import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ApprovalsPage from "./page";

const reviewList = {
  context: {
    schema_version: "1.0" as const,
    merchant_id: "merchant_demo_001",
    merchant_display_name: "Demo merchant",
    environment: "TEST" as const,
    data_classification: "TEST" as const,
    as_of: "2026-08-29T06:00:00Z",
  },
  reviews: [
    {
      review_id: "review_001",
      case_id: "case_001",
      customer_reference_masked: "CUSTOMER · 1234",
      amount_minor: 25_000,
      currency: "INR",
      proposed_action_type: "RETRY_PAYMENT",
      diagnosis: "INSUFFICIENT_FUNDS",
      confidence_basis_points: 8_400,
      reason_code: "HIGH_VALUE",
      risk_detail: "Merchant approval required",
      policy_version: "policy_001",
      classification: "TEST" as const,
      requested_at: "2026-08-29T06:00:00Z",
      expires_at: "2099-08-30T06:00:00Z",
    },
  ],
  total: 1,
};

describe("approvals page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("collects a rationale in-page and sends the protected uppercase decision", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json(reviewList))
      .mockResolvedValueOnce(Response.json({ case_state: "READY" }))
      .mockResolvedValueOnce(Response.json({ ...reviewList, reviews: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ApprovalsPage />);
    await screen.findByRole("button", { name: "Approve" });

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "Confirm approve" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Rationale"), {
      target: { value: "The verified evidence supports a bounded retry." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/dashboard/reviews/review_001/decision");
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      decision: "APPROVE",
      rationale: "The verified evidence supports a bounded retry.",
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("marks an expired review as expired and prevents an approval decision", async () => {
    const expiredReviewList = {
      ...reviewList,
      reviews: [
        {
          ...reviewList.reviews[0],
          expires_at: "2026-08-30T06:00:00Z",
        },
      ],
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(expiredReviewList));
    vi.stubGlobal("fetch", fetchMock);

    render(<ApprovalsPage />);

    await screen.findByText("EXPIRED");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
