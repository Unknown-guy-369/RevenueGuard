import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import {
  DashboardNav,
  DataUnavailable,
  StateBadge,
  formatMoney,
  formatMoment,
  humanize,
  resultData,
  resultMessage,
} from "@/components/dashboard-ui";
import { LiveRefresh } from "@/components/live-refresh";
import { getLiveCase } from "@/lib/api/live";
import { getDashboardSession } from "@/lib/auth/session";

type CasePageProps = { params: Promise<{ caseId: string }> };
export const dynamic = "force-dynamic";

export default async function CasePage({ params }: CasePageProps) {
  if ((await getDashboardSession()) === null) redirect("/sign-in");
  const { caseId } = await params;
  const result = await getLiveCase(caseId);
  if (result.kind === "not-found") notFound();
  const detail = resultData(result);
  if (detail === null) {
    return (
      <main className="dashboard-shell">
        <DashboardNav merchantName="Configured merchant" />
        <section className="dashboard-main">
          <DataUnavailable message={resultMessage(result) ?? "The case response was incomplete."} />
        </section>
      </main>
    );
  }
  const item = detail.case;
  return (
    <main className="dashboard-shell">
      <DashboardNav merchantName={detail.context.merchant_display_name} />
      <section className="dashboard-main case-detail-page">
        <div className="detail-toolbar">
          <Link href="/dashboard">← All cases</Link>
          <LiveRefresh />
        </div>
        <header className="case-hero">
          <div>
            <span className="eyebrow">{item.subject_reference_masked}</span>
            <h1>{humanize(item.workflow_type)}</h1>
            <p>
              {item.diagnosis ? humanize(item.diagnosis) : "Diagnosis pending"} · version{" "}
              {item.state_version}
            </p>
          </div>
          <div className="case-hero-meta">
            <StateBadge state={item.state} />
            <strong>{formatMoney(item.revenue_at_risk_minor, item.currency)}</strong>
            <small>Revenue at risk</small>
          </div>
        </header>

        <section className="case-detail-grid">
          <article className="panel timeline-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">EVIDENCE-LINKED STATE</span>
                <h2>Case timeline</h2>
              </div>
              <span>{detail.transitions.length} transitions</span>
            </div>
            <ol className="timeline">
              {detail.transitions.map((transition) => (
                <li key={transition.transition_id}>
                  <span className="timeline-marker" />
                  <div>
                    <div className="timeline-title">
                      <strong>{humanize(transition.to_state)}</strong>
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
              ))}
            </ol>
          </article>

          <aside className="detail-side">
            <article className="panel compact-panel">
              <span className="eyebrow">COUNTERS</span>
              <dl>
                <div>
                  <dt>Retries</dt>
                  <dd>{item.retry_count}</dd>
                </div>
                <div>
                  <dt>Contacts</dt>
                  <dd>{item.contact_count}</dd>
                </div>
                <div>
                  <dt>Confidence</dt>
                  <dd>
                    {item.diagnosis_confidence_basis_points === null
                      ? "—"
                      : `${(item.diagnosis_confidence_basis_points / 100).toFixed(1)}%`}
                  </dd>
                </div>
              </dl>
            </article>
            <article className="panel compact-panel">
              <span className="eyebrow">MODEL TRACE</span>
              <h2>
                {detail.predictions.length
                  ? `${detail.predictions.length} bounded nodes`
                  : "Deterministic path"}
              </h2>
              {detail.predictions.map((prediction) => (
                <div className="trace-row" key={prediction.prediction_id}>
                  <span>{humanize(prediction.node)}</span>
                  <StateBadge state={prediction.status} />
                  <small>{prediction.failure_code ?? `${prediction.latency_ms} ms`}</small>
                </div>
              ))}
            </article>
          </aside>
        </section>

        <section className="evidence-grid">
          <article className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">DECISIONS</span>
                <h2>Policy receipts</h2>
              </div>
              <span>{detail.decisions.length}</span>
            </div>
            {detail.decisions.length ? (
              detail.decisions.map((decision) => (
                <div className="evidence-row" key={decision.decision_id}>
                  <div>
                    <strong>{humanize(decision.selected_action_type)}</strong>
                    <p>{decision.explanation}</p>
                    <small>{decision.policy_reason_codes.map(humanize).join(" · ")}</small>
                  </div>
                  <div>
                    <StateBadge state={decision.policy_result} />
                    <code>{decision.policy_version}</code>
                  </div>
                </div>
              ))
            ) : (
              <p className="empty-copy">No decision receipt is stored for this case yet.</p>
            )}
          </article>
          <article className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">ACTIONS & OUTCOMES</span>
                <h2>Execution evidence</h2>
              </div>
              <span>{detail.actions.length}</span>
            </div>
            {detail.actions.length ? (
              detail.actions.map((action) => {
                const outcome = detail.outcomes.find(
                  (candidate) => candidate.action_id === action.action_id,
                );
                return (
                  <div className="evidence-row" key={action.action_id}>
                    <div>
                      <strong>{humanize(action.action_type)}</strong>
                      <p>{action.target_reference_masked}</p>
                      <code>{action.idempotency_key}</code>
                    </div>
                    <div>
                      <StateBadge state={outcome?.status ?? action.status} />
                      {outcome?.is_authoritative ? (
                        <small>
                          Authoritative ·{" "}
                          {formatMoney(outcome.recovered_amount_minor, outcome.currency)}
                        </small>
                      ) : (
                        <small>
                          {outcome?.evidence_source ??
                            `${action.attempt_count}/${action.max_attempts} attempts`}
                        </small>
                      )}
                    </div>
                  </div>
                );
              })
            ) : (
              <p className="empty-copy">
                No action was authorized. The policy result may have deferred, stopped, or skipped
                execution.
              </p>
            )}
          </article>
        </section>
        {detail.reviews.length ? (
          <section className="panel review-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">HUMAN GOVERNANCE</span>
                <h2>Review requests</h2>
              </div>
              <span>{detail.reviews.length}</span>
            </div>
            {detail.reviews.map((review) => (
              <div className="evidence-row" key={review.review_id}>
                <div>
                  <strong>{humanize(review.proposed_action_type)}</strong>
                  <p>{review.risk_detail}</p>
                  <small>
                    {humanize(review.reason_code)} · expires {formatMoment(review.expires_at)}
                  </small>
                </div>
                <div>
                  <StateBadge state={review.status} />
                  <code>{review.policy_version}</code>
                </div>
              </div>
            ))}
          </section>
        ) : null}
      </section>
    </main>
  );
}
