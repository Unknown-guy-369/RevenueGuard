"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { formatMoney, humanize } from "@/components/dashboard-ui";
import { fetchJson, type RecoveryOverview } from "@/lib/api/merchant-contracts";
import type { LiveCaseList } from "@/lib/api/live-contracts";

type RecoveryData = { overview: RecoveryOverview; cases: LiveCaseList["cases"] };

export default function RecoveryOverviewPage() {
  const [data, setData] = useState<RecoveryData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetchJson<RecoveryOverview>("/api/v1/dashboard/recovery-overview"),
      fetchJson<LiveCaseList>("/api/v1/dashboard/cases?limit=50"),
    ])
      .then(([overview, cases]) => {
        if (active) setData({ overview, cases: cases.cases });
      })
      .catch((reason: unknown) => {
        if (active)
          setError(reason instanceof Error ? reason.message : "Recovery data unavailable");
      });
    return () => {
      active = false;
    };
  }, []);

  if (error) return <DataState title="Recovery data is unavailable" detail={error} />;
  if (!data)
    return (
      <DataState title="Loading recovery portfolio" detail="Reading authoritative case state…" />
    );

  return (
    <div className="space-y-6">
      <header className="border-b border-gray-200 pb-4">
        <h1 className="text-3xl font-heading font-medium tracking-tight text-ledger-navy">
          Agent Recovery
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Bounded reasoning with deterministic policy enforcement.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {data.overview.currency_totals.map((totals) => (
          <div className="contents" key={totals.currency}>
            <MetricCard
              label={`Revenue at risk · ${totals.currency}`}
              value={formatMoney(totals.revenue_at_risk_minor, totals.currency)}
            />
            <MetricCard
              label={`Verified gross · ${totals.currency}`}
              value={formatMoney(totals.verified_gross_recovered_minor, totals.currency)}
            />
            <MetricCard
              label="Recovery costs"
              value={
                totals.recovery_cost_minor === null
                  ? "Unavailable"
                  : formatMoney(totals.recovery_cost_minor, totals.currency)
              }
            />
            <MetricCard
              label="Verified net"
              value={
                totals.verified_net_recovered_minor === null
                  ? "Unavailable"
                  : formatMoney(totals.verified_net_recovered_minor, totals.currency)
              }
            />
          </div>
        ))}
        {!data.overview.currency_totals.length ? (
          <MetricCard label="Revenue at risk" value="No active currency totals" />
        ) : null}
      </div>
      {!data.overview.cost_data_available ? (
        <p className="text-xs text-gray-500">
          Net recovery is not calculated until authoritative intervention-cost data is available.
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatusCard label="Active cases" value={data.overview.active_cases} />
        <StatusCard label="Deferred cases" value={data.overview.deferred_cases} tone="warning" />
        <StatusCard label="Unknown outcomes" value={data.overview.unknown_cases} tone="warning" />
        <StatusCard label="Pending approvals" value={data.overview.pending_reviews} />
        <StatusCard label="Active incidents" value={data.overview.active_incidents} tone="danger" />
      </div>

      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-6 py-5">
          <h2 className="text-lg font-heading font-medium">Recovery cases</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-6 py-4">Subject</th>
                <th className="px-6 py-4">Workflow</th>
                <th className="px-6 py-4 text-right">At risk</th>
                <th className="px-6 py-4">Diagnosis</th>
                <th className="px-6 py-4">State</th>
                <th className="px-6 py-4">Updated</th>
                <th className="px-6 py-4">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.cases.map((item) => (
                <tr className="transition-colors hover:bg-gray-50" key={item.case_id}>
                  <td className="px-6 py-4 font-mono text-gray-700">
                    {item.subject_reference_masked}
                    {item.classification === "SYNTHETIC" ? (
                      <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
                        SYNTHETIC
                      </span>
                    ) : null}
                  </td>
                  <td className="px-6 py-4 text-gray-600">{humanize(item.workflow_type)}</td>
                  <td className="px-6 py-4 text-right font-mono font-medium">
                    {formatMoney(item.revenue_at_risk_minor, item.currency)}
                  </td>
                  <td className="max-w-52 truncate px-6 py-4 text-gray-600">
                    {item.diagnosis ? humanize(item.diagnosis) : "Pending"}
                  </td>
                  <td className="px-6 py-4">{humanize(item.state)}</td>
                  <td className="whitespace-nowrap px-6 py-4 text-gray-500">
                    {new Date(item.updated_at).toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <Link
                      aria-label={`Open case ${item.case_id}`}
                      className="inline-flex text-gray-400 hover:text-payment-blue"
                      href={`/dashboard/recovery/cases/${item.case_id}`}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Link>
                  </td>
                </tr>
              ))}
              {!data.cases.length ? (
                <tr>
                  <td className="px-6 py-12 text-center text-gray-500" colSpan={7}>
                    No recovery cases match the current merchant scope.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <span className="text-sm font-medium text-gray-500">{label}</span>
      <span className="mt-2 text-2xl font-medium tracking-tight text-gray-900">{value}</span>
    </div>
  );
}

function StatusCard({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "warning" | "danger";
}) {
  const colors =
    tone === "danger"
      ? "border-red-100 bg-red-50/50 text-critical-red"
      : tone === "warning"
        ? "border-amber-100 bg-amber-50/50 text-risk-amber"
        : "border-gray-200 bg-gray-50/50 text-gray-900";
  return (
    <div className={`flex flex-col rounded-xl border p-5 ${colors}`}>
      <span className="text-sm font-medium text-gray-600">{label}</span>
      <span className="mt-2 font-mono text-2xl font-medium">{value.toLocaleString()}</span>
    </div>
  );
}

function DataState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-10">
      <h1 className="text-xl font-medium">{title}</h1>
      <p className="mt-2 text-sm text-gray-500">{detail}</p>
    </div>
  );
}
