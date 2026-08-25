import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusRail } from "@/components/status-rail";

describe("StatusRail", () => {
  it("renders bounded and verification stages accessibly", () => {
    render(
      <StatusRail
        steps={[
          { label: "Decision bounded", detail: "Policy authorizes.", state: "ready" },
          { label: "Outcome verified", detail: "Evidence resolves it.", state: "pending" },
        ]}
      />,
    );

    expect(screen.getByRole("list", { name: "Revenue recovery control stages" })).toBeVisible();
    expect(screen.getByText("Decision bounded")).toBeVisible();
    expect(screen.getByText("Bounded")).toBeVisible();
    expect(screen.getByText("Verify")).toBeVisible();
  });
});
