import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CaseTimeline } from "./case-timeline";

const transitions = [
  {
    transition_id: "transition-1",
    to_state: "DIAGNOSING",
    reason_code: "DIAGNOSIS_STARTED",
    reason_detail: null,
    actor_reference_masked: "ACTOR · 1234",
    policy_version: "policy-1",
    authoritative_evidence_reference: null,
    occurred_at: "2026-08-27T17:00:00Z",
  },
  {
    transition_id: "transition-2",
    to_state: "DECISION_PENDING",
    reason_code: "DIAGNOSIS_COMPLETED",
    reason_detail: null,
    actor_reference_masked: "ACTOR · 1234",
    policy_version: "policy-1",
    authoritative_evidence_reference: null,
    occurred_at: "2026-08-27T17:01:00Z",
  },
  {
    transition_id: "transition-3",
    to_state: "VERIFYING",
    reason_code: "ACTION_SUCCEEDED",
    reason_detail: null,
    actor_reference_masked: "ACTOR · 5678",
    policy_version: "policy-1",
    authoritative_evidence_reference: "simulator/example/pending",
    occurred_at: "2026-08-27T17:02:00Z",
  },
];

describe("CaseTimeline", () => {
  it("marks earlier transitions completed and the latest transition current", () => {
    const { container } = render(<CaseTimeline transitions={transitions} />);

    expect(screen.getAllByText("Completed")).toHaveLength(2);
    expect(screen.getByText("Current stage")).toBeInTheDocument();
    expect(container.querySelectorAll(".timeline-step-completed")).toHaveLength(2);

    const currentStep = container.querySelector('[aria-current="step"]');
    expect(currentStep).toHaveTextContent("Verifying");
    expect(currentStep).toHaveTextContent("Action succeeded");
  });
});
