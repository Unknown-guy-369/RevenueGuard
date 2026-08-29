import { formatMoment, humanize } from "@/components/dashboard-ui";

type CaseTransition = {
  transition_id: string;
  to_state: string;
  reason_code: string;
  reason_detail: string | null;
  actor_reference_masked: string;
  policy_version: string;
  authoritative_evidence_reference: string | null;
  occurred_at: string;
};

type CaseTimelineProps = {
  transitions: CaseTransition[];
};

export function CaseTimeline({ transitions }: CaseTimelineProps) {
  const currentIndex = transitions.length - 1;

  return (
    <ol className="timeline">
      {transitions.map((transition, index) => {
        const isCurrent = index === currentIndex;

        return (
          <li
            className={isCurrent ? "timeline-step-current" : "timeline-step-completed"}
            key={transition.transition_id}
            aria-current={isCurrent ? "step" : undefined}
          >
            <span className="timeline-marker" aria-hidden="true" />
            <div>
              <div className="timeline-title">
                <div className="timeline-title-state">
                  <strong>{humanize(transition.to_state)}</strong>
                  <span className="timeline-stage-label">
                    {isCurrent ? "Current stage" : "Completed"}
                  </span>
                </div>
                <time dateTime={transition.occurred_at}>
                  {formatMoment(transition.occurred_at)}
                </time>
              </div>
              <p>
                {humanize(transition.reason_code)}
                {transition.reason_detail ? ` — ${transition.reason_detail}` : ""}
              </p>
              <small>
                Policy {transition.policy_version} · {transition.actor_reference_masked}
              </small>
              {transition.authoritative_evidence_reference ? (
                <code>{transition.authoritative_evidence_reference}</code>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
