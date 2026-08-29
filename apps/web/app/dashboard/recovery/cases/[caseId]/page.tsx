import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ChevronDown, ChevronLeft } from "lucide-react";

import {
  DataUnavailable,
  StateBadge,
  formatMoney,
  formatMoment,
  humanize,
  resultData,
  resultMessage,
} from "@/components/dashboard-ui";
import { CaseTimeline } from "@/components/case-timeline";
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
      <main className="p-8">
        <DataUnavailable message={resultMessage(result) ?? "The case response was incomplete."} />
      </main>
    );
  }

  const item = detail.case;
  const recentDecision = detail.decisions[0];
  const activeOutcome = detail.outcomes[0];

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between border-b border-gray-200 pb-4">
        <div className="flex items-center gap-4">
          <Link
            href="/dashboard/recovery"
            className="p-2 text-gray-400 hover:text-ledger-navy hover:bg-gray-100 rounded-full transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-heading text-ledger-navy font-medium tracking-tight">
              Case {caseId.slice(0, 8)}
            </h1>
            <p className="text-sm text-gray-500 mt-1 font-mono">{item.subject_reference_masked}</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {item.classification === "SYNTHETIC" ? (
            <span className="rounded bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-800">
              SYNTHETIC
            </span>
          ) : null}
          <StateBadge state={item.state} />
          <LiveRefresh />
        </div>
      </div>

      {/* 1. Case summary */}
      <section className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <h2 className="text-lg font-heading font-medium text-ledger-navy mb-4">Case Summary</h2>
        <div className="grid grid-cols-4 gap-6">
          <div>
            <div className="text-sm text-gray-500">Workflow</div>
            <div className="font-medium text-gray-900 mt-1">{humanize(item.workflow_type)}</div>
          </div>
          <div>
            <div className="text-sm text-gray-500">Revenue at risk</div>
            <div className="font-mono font-medium text-gray-900 mt-1">
              {formatMoney(item.revenue_at_risk_minor, item.currency)}
            </div>
          </div>
          <div>
            <div className="text-sm text-gray-500">Updated</div>
            <div className="text-gray-900 mt-1">{formatMoment(item.updated_at)}</div>
          </div>
        </div>
      </section>

      {/* 2. Agent diagnosis */}
      <section className="bg-blue-50/50 rounded-xl border border-payment-blue/20 p-6">
        <h2 className="text-lg font-heading font-medium text-ledger-navy mb-4 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-payment-blue" /> Agent Diagnosis
        </h2>
        <p className="text-gray-700 leading-relaxed text-sm">
          {item.diagnosis
            ? `The agent identified the issue as ${humanize(item.diagnosis)}. Given the current context and evidence, this failure is likely caused by temporary factors.`
            : "Diagnosis is currently pending for this case."}
        </p>
      </section>

      <div className="grid grid-cols-2 gap-6">
        {/* 3. Recommended action */}
        <section className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <h2 className="text-lg font-heading font-medium text-ledger-navy mb-4">Recommendation</h2>
          {recentDecision ? (
            <div>
              <div className="font-medium text-gray-900">
                {humanize(recentDecision.selected_action_type)}
              </div>
              <p className="text-sm text-gray-600 mt-2">{recentDecision.explanation}</p>
            </div>
          ) : (
            <p className="text-sm text-gray-500 italic">
              No recommendations have been generated yet.
            </p>
          )}
        </section>

        {/* 4. Policy and safety checks */}
        <section className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <h2 className="text-lg font-heading font-medium text-ledger-navy mb-4">Policy Checks</h2>
          {recentDecision ? (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm text-gray-500">Result:</span>
                <StateBadge state={recentDecision.policy_result} />
              </div>
              <ul className="list-disc pl-4 text-sm text-gray-600 space-y-1">
                {recentDecision.policy_reason_codes.map((code: string) => (
                  <li key={code}>{humanize(code)}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-gray-500 italic">Awaiting policy evaluation.</p>
          )}
        </section>
      </div>

      {/* 5. Timeline and authoritative outcome */}
      <section className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
        <h2 className="text-lg font-heading font-medium text-ledger-navy mb-6">
          Timeline & Outcome
        </h2>
        <div className="mb-8 p-4 bg-gray-50 rounded-lg border border-gray-100 flex items-center justify-between">
          <div>
            <div className="text-sm font-medium text-gray-900">Authoritative Outcome</div>
            <div className="text-sm text-gray-500 mt-1">
              {activeOutcome?.is_authoritative
                ? `Verified from ${activeOutcome.evidence_source}`
                : "No verified outcome recorded yet."}
            </div>
          </div>
          <div>
            {activeOutcome?.is_authoritative && activeOutcome.recovered_amount_minor > 0 ? (
              <span className="font-mono text-lg font-medium text-verified-green">
                +{formatMoney(activeOutcome.recovered_amount_minor, activeOutcome.currency)}
              </span>
            ) : (
              <span className="text-sm text-gray-400">Pending</span>
            )}
          </div>
        </div>
        <CaseTimeline transitions={detail.transitions} />
      </section>

      {/* Technical Evidence (Expandable) */}
      <details className="bg-white rounded-xl border border-gray-200 shadow-sm group">
        <summary className="px-6 py-4 cursor-pointer font-medium text-gray-700 flex items-center justify-between">
          Technical Evidence
          <span className="text-gray-400 group-open:rotate-180 transition-transform">
            <ChevronDown className="w-5 h-5" />
          </span>
        </summary>
        <div className="px-6 pb-6 pt-2 border-t border-gray-100 text-sm space-y-6">
          <div>
            <h3 className="font-medium text-gray-900 mb-2">Model Trace</h3>
            {detail.predictions.length ? (
              <div className="space-y-2">
                {detail.predictions.map((p) => (
                  <div
                    key={p.prediction_id}
                    className="flex justify-between p-2 bg-gray-50 rounded font-mono text-xs text-gray-600"
                  >
                    <span>{p.node}</span>
                    <span>
                      {p.latency_ms}ms · {p.status}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-gray-500">Deterministic path - no model invocations.</div>
            )}
          </div>

          <div>
            <h3 className="font-medium text-gray-900 mb-2">Counters</h3>
            <div className="grid grid-cols-2 gap-4 bg-gray-50 p-4 rounded-lg font-mono text-xs">
              <div>Retries: {item.retry_count}</div>
              <div>Contacts: {item.contact_count}</div>
              <div>
                Confidence:{" "}
                {item.diagnosis_confidence_basis_points
                  ? `${(item.diagnosis_confidence_basis_points / 100).toFixed(1)}%`
                  : "—"}
              </div>
              <div>Version: {item.state_version}</div>
            </div>
          </div>
        </div>
      </details>
    </div>
  );
}
