export type StatusStep = {
  label: string;
  detail: string;
  state: "ready" | "pending";
};

type StatusRailProps = {
  steps: StatusStep[];
};

export function StatusRail({ steps }: StatusRailProps) {
  return (
    <ol className="status-rail" aria-label="Revenue recovery control stages">
      {steps.map((step, index) => (
        <li className="status-step" key={step.label}>
          <span className={`status-marker status-marker-${step.state}`} aria-hidden="true">
            {String(index + 1).padStart(2, "0")}
          </span>
          <div>
            <strong>{step.label}</strong>
            <p>{step.detail}</p>
          </div>
          <span className={`status-label status-label-${step.state}`}>
            {step.state === "ready" ? "Bounded" : "Verify"}
          </span>
        </li>
      ))}
    </ol>
  );
}
