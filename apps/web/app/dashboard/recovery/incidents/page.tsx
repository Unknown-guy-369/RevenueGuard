"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Clock } from "lucide-react";

import { fetchJson, type IncidentList } from "@/lib/api/merchant-contracts";

export default function IncidentsPage() {
  const [data, setData] = useState<IncidentList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchJson<IncidentList>("/api/v1/dashboard/incidents?active_only=true")
      .then((value) => {
        if (active) setData(value);
      })
      .catch((reason: unknown) => {
        if (active)
          setError(reason instanceof Error ? reason.message : "Incident data unavailable");
      });
    return () => {
      active = false;
    };
  }, []);

  if (error) return <DataState title="Incident status is unknown" detail={error} />;
  if (!data)
    return (
      <DataState
        title="Loading portfolio intelligence"
        detail="Checking current incident records…"
      />
    );

  return (
    <div className="space-y-6">
      <header className="border-b border-gray-200 pb-4">
        <h1 className="text-3xl font-heading font-medium tracking-tight text-ledger-navy">
          Portfolio Intelligence
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Cross-case coordination and systemic incident detection.
        </p>
      </header>
      <div className="space-y-4">
        {data.incidents.map((incident) => (
          <article
            className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm"
            key={incident.incident_id}
          >
            <div className="flex items-center justify-between bg-ledger-navy px-6 py-4 text-white">
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-risk-amber" />
                <h2 className="text-lg font-heading font-medium">
                  {incident.payment_method ?? "All methods"} ·{" "}
                  {incident.issuer_family ?? "All issuers"} ·{" "}
                  {incident.error_family ?? "Unclassified"}
                </h2>
              </div>
              <span className="rounded-full bg-white/10 px-3 py-1 text-xs font-mono uppercase tracking-wide">
                {incident.status}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-6 p-6 sm:grid-cols-2 xl:grid-cols-4">
              <Measure
                label="Normal failure rate"
                value={`${(incident.baseline_failure_rate_basis_points / 100).toFixed(1)}%`}
              />
              <Measure
                danger
                label="Current failure rate"
                value={`${(incident.current_failure_rate_basis_points / 100).toFixed(1)}%`}
              />
              <Measure
                label="Affected payments"
                value={incident.affected_payments.toLocaleString()}
              />
              <Measure
                warning
                label="Retries paused"
                value={incident.paused_cases.toLocaleString()}
              />
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t border-gray-200 bg-gray-50 px-6 py-4 text-sm text-gray-600">
              <Clock className="h-4 w-4 text-gray-400" />
              Started{" "}
              <span className="font-mono text-gray-900">
                {new Date(incident.starts_at).toLocaleString()}
              </span>
              <span aria-hidden>·</span>
              {incident.healthy_windows} healthy windows <span aria-hidden>·</span>threshold{" "}
              {incident.threshold_version}
            </div>
          </article>
        ))}
        {!data.incidents.length ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-green-50">
              <div className="h-2 w-2 rounded-full bg-verified-green shadow-[0_0_0_4px_rgba(22,130,93,0.2)]" />
            </div>
            <h2 className="text-lg font-medium text-gray-900">No active incidents</h2>
            <p className="mt-2 max-w-md text-gray-500">
              The authoritative incident store has no active payment degradation for this merchant.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Measure({
  label,
  value,
  danger = false,
  warning = false,
}: {
  label: string;
  value: string;
  danger?: boolean;
  warning?: boolean;
}) {
  return (
    <div>
      <div className="text-sm text-gray-500">{label}</div>
      <div
        className={`mt-1 font-mono text-xl font-medium ${danger ? "text-critical-red" : warning ? "text-risk-amber" : "text-gray-900"}`}
      >
        {value}
      </div>
    </div>
  );
}

function DataState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 p-10">
      <h1 className="text-xl font-medium text-gray-900">{title}</h1>
      <p className="mt-2 text-sm text-gray-600">{detail}</p>
    </div>
  );
}
