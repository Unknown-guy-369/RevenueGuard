import Link from "next/link";

import type { ApiResult } from "@/lib/api/client";
import type { LiveCaseSummary } from "@/lib/api/live-contracts";

const stateOrder = [
  "DETECTED",
  "DIAGNOSING",
  "DECISION_PENDING",
  "POLICY_CHECK",
  "READY",
  "EXECUTING",
  "VERIFYING",
  "RECOVERED",
] as const;

export function resultData<T>(result: ApiResult<T>): T | null {
  return result.kind === "ok" || result.kind === "degraded" ? result.data : null;
}

export function resultMessage<T>(result: ApiResult<T>): string | null {
  if (result.kind === "unavailable" || result.kind === "invalid-contract") return result.message;
  if (result.kind === "unauthenticated")
    return "The internal dashboard API rejected its credential.";
  if (result.kind === "forbidden") return "This operator cannot access the configured merchant.";
  if (result.kind === "not-found") return "The configured merchant was not found.";
  if (result.kind === "degraded") return result.message;
  return null;
}

export function formatMoney(amountMinor: number, currency: string): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(amountMinor / 100);
}

export function formatMoment(value: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

export function humanize(value: string): string {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (character) => character.toUpperCase());
}

export function StateBadge({ state }: { state: string }) {
  const caution = ["UNKNOWN", "ESCALATED", "DEFERRED", "STOPPED"].includes(state);
  const positive = state === "RECOVERED";
  return (
    <span className={`state-badge ${positive ? "state-positive" : caution ? "state-caution" : ""}`}>
      {humanize(state)}
    </span>
  );
}

export function RecoveryRail({ state }: { state: string }) {
  const terminalIndex = stateOrder.indexOf(state as (typeof stateOrder)[number]);
  const fallbackIndex = ["UNKNOWN", "DEFERRED", "ESCALATED", "STOPPED"].includes(state) ? 5 : 0;
  const activeIndex = terminalIndex >= 0 ? terminalIndex : fallbackIndex;
  const thresholds = [0, 3, 5, 7];
  const labels = ["Detected", "Governed", "Executing", "Verified"];
  return (
    <div className="recovery-rail" aria-label={`Recovery progress: ${humanize(state)}`}>
      {thresholds.map((threshold, index) => (
        <span
          className={activeIndex >= threshold ? "rail-step rail-complete" : "rail-step"}
          key={labels[index]}
          title={labels[index]}
        />
      ))}
    </div>
  );
}

export function CaseTable({ cases }: { cases: LiveCaseSummary[] }) {
  if (cases.length === 0) {
    return (
      <div className="empty-state">
        No recovery cases match this view. Replay a Test Mode failure to begin.
      </div>
    );
  }
  return (
    <div className="case-table" role="table" aria-label="Recovery cases">
      <div className="case-table-head" role="row">
        <span>Case</span>
        <span>Flow</span>
        <span>Exposure</span>
        <span>State</span>
        <span>Updated</span>
      </div>
      {cases.map((item) => (
        <Link
          className="case-table-row"
          href={`/dashboard/cases/${encodeURIComponent(item.case_id)}`}
          key={item.case_id}
          role="row"
        >
          <span className="case-identity">
            <strong>{humanize(item.workflow_type)}</strong>
            <small>{item.subject_reference_masked}</small>
          </span>
          <RecoveryRail state={item.state} />
          <span className="money-cell">
            {formatMoney(item.revenue_at_risk_minor, item.currency)}
          </span>
          <StateBadge state={item.state} />
          <time dateTime={item.updated_at}>{formatMoment(item.updated_at)}</time>
        </Link>
      ))}
    </div>
  );
}

export function DashboardNav({ merchantName }: { merchantName: string }) {
  return (
    <header className="dashboard-nav">
      <Link className="wordmark" href="/dashboard">
        <span className="wordmark-mark" aria-hidden="true">
          R
        </span>
        RevenueGuard
      </Link>
      <div className="merchant-chip">
        <span>TEST</span>
        {merchantName}
      </div>
      <nav aria-label="Dashboard navigation">
        <Link href="/dashboard">Control room</Link>
      </nav>
      <form action="/api/session/sign-out" method="post">
        <button type="submit">Sign out</button>
      </form>
    </header>
  );
}

export function DataUnavailable({ message }: { message: string }) {
  return (
    <div className="notice notice-danger" role="alert">
      <strong>Authoritative data is unavailable.</strong>
      <span>{message}</span>
      <span>
        Confirm the API, database, merchant scope, and dashboard credentials, then refresh.
      </span>
    </div>
  );
}
