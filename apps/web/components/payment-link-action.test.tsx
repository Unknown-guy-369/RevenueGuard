import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PaymentLinkAction } from "@/components/payment-link-action";

describe("PaymentLinkAction", () => {
  afterEach(cleanup);

  it("opens a completed Razorpay Test payment link in a separate tab", () => {
    render(
      <PaymentLinkAction
        action={{
          action_type: "CREATE_PAYMENT_LINK",
          status: "SUCCEEDED",
          payment_link_url: "https://rzp.io/i/test-payment",
        }}
      />,
    );

    const link = screen.getByRole("link", { name: "Open Razorpay Test payment link" });
    expect(link).toHaveAttribute("href", "https://rzp.io/i/test-payment");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("does not expose a link until a payment-link action succeeds", () => {
    render(
      <PaymentLinkAction
        action={{
          action_type: "CREATE_PAYMENT_LINK",
          status: "UNKNOWN",
          payment_link_url: "https://rzp.io/i/test-payment",
        }}
      />,
    );

    expect(screen.queryByRole("link", { name: "Open Razorpay Test payment link" })).toBeNull();
  });
});
