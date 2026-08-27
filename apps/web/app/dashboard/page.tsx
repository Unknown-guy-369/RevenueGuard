import { redirect } from "next/navigation";

import {
  CaseTable,
  DashboardNav,
  DataUnavailable,
  formatMoney,
  formatMoment,
  resultData,
  resultMessage,
} from "@/components/dashboard-ui";
import { LiveRefresh } from "@/components/live-refresh";
import { getLiveCases, getLiveOperationsHealth, getLiveOverview } from "@/lib/api/live";
import { getDashboardSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  if ((await getDashboardSession()) === null) redirect("/sign-in");
  const [overviewResult, casesResult, healthResult] = await Promise.all([
    getLiveOverview(),
    getLiveCases(),
    getLiveOperationsHealth(),
  ]);
  const overview = resultData(overviewResult);
  const cases = resultData(casesResult);
  const health = resultData(healthResult);
  const error =
    resultMessage(overviewResult) ?? resultMessage(casesResult) ?? resultMessage(healthResult);

  if (overview === null || cases === null || health === null) {
    return (
      <main className="dashboard-shell">
        <DashboardNav merchantName="Configured merchant" />
        <section className="dashboard-main">
          <DataUnavailable message={error ?? "The dashboard response was incomplete."} />
        </section>
      </main>
    );
  }

  const primaryCurrency = overview.currency_totals[0];
  const modelTotal = overview.counts.model_succeeded + overview.counts.model_fallback;
  return (
    <main className="dashboard-shell">
      <DashboardNav merchantName={overview.context.merchant_display_name} />
      <section className="dashboard-main">
        <div className="dashboard-heading">
          <div>
            <span className="eyebrow">RECOVERY CONTROL ROOM</span>
            <h1>Money moves only after proof.</h1>
            <p>
              Live Test Mode state from PostgreSQL. No synthetic success and no guessed outcomes.
            </p>
          </div>
          <div className="live-stack">
            <LiveRefresh />
            <small>As of {formatMoment(overview.context.as_of)}</small>
          </div>
        </div>

        {error ? <div className="notice">{error}</div> : null}
        <section className="metric-strip" aria-label="Recovery totals">
          <article className="metric-primary">
            <span>Revenue currently at risk</span>
            <strong>
              {primaryCurrency
                ? formatMoney(primaryCurrency.revenue_at_risk_minor, primaryCurrency.currency)
                : "—"}
            </strong>
            <small>{overview.counts.active_cases} active cases across the control plane</small>
          </article>
          <article>
            <span>Verified recovered</span>
            <strong>
              {primaryCurrency
                ? formatMoney(primaryCurrency.verified_recovered_minor, primaryCurrency.currency)
                : "—"}
            </strong>
            <small>{overview.counts.recovered_cases} cases reached authoritative recovery</small>
          </article>
          <article>
            <span>Needs attention</span>
            <strong>
              {overview.counts.unknown_cases +
                overview.counts.escalated_cases +
                overview.counts.deferred_cases +
                overview.counts.pending_reviews}
            </strong>
            <small>
              {overview.counts.unknown_cases} unknown · {overview.counts.pending_reviews} human
              reviews
            </small>
          </article>
        </section>
        {overview.currency_totals.length > 1 ? (
          <div className="currency-breakdown" aria-label="Currency-specific recovery totals">
            {overview.currency_totals.map((total) => (
              <span key={total.currency}>
                <strong>{total.currency}</strong> at risk{" "}
                {formatMoney(total.revenue_at_risk_minor, total.currency)} · verified{" "}
                {formatMoney(total.verified_recovered_minor, total.currency)}
              </span>
            ))}
          </div>
        ) : null}

        <section className="dashboard-grid">
          <article className="panel cases-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">LIVE WORKFLOWS</span>
                <h2>Recovery cases</h2>
              </div>
              <span>{cases.total} total</span>
            </div>
            <CaseTable cases={cases.cases} />
          </article>
          <aside className="dashboard-side">
            <article
              className={`panel health-panel ${health.status === "DEGRADED" ? "health-degraded" : ""}`}
            >
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">OPERATIONS</span>
                  <h2>{health.status === "HEALTHY" ? "System clear" : "Review required"}</h2>
                </div>
                <span className="pulse-dot" />
              </div>
              <dl>
                <div>
                  <dt>Pending events</dt>
                  <dd>{health.pending_events}</dd>
                </div>
                <div>
                  <dt>Pending actions</dt>
                  <dd>{health.pending_actions}</dd>
                </div>
                <div>
                  <dt>Unknown actions</dt>
                  <dd>{health.unknown_actions}</dd>
                </div>
                <div>
                  <dt>Dead letters</dt>
                  <dd>{health.dead_letter_events}</dd>
                </div>
              </dl>
            </article>
            <article className="panel intelligence-panel">
              <span className="eyebrow">BOUNDED INTELLIGENCE</span>
              <h2>
                {modelTotal === 0
                  ? "Waiting for a new case"
                  : `${overview.counts.model_succeeded}/${modelTotal} model nodes passed`}
              </h2>
              <p>
                {overview.counts.model_fallback} deterministic fallbacks. Policy remains the final
                authority for every action.
              </p>
              <div className="model-meter">
                <span
                  style={{
                    width: `${modelTotal ? (overview.counts.model_succeeded / modelTotal) * 100 : 0}%`,
                  }}
                />
              </div>
              <small>{overview.counts.decision_receipts} persisted decision receipts</small>
            </article>
          </aside>
        </section>
      </section>
    </main>
  );
}
