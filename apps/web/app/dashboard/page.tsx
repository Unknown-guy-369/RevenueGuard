"use client";

import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { ProportionalBar } from "@/components/ProportionalBar";
import { RevenueMovementChart } from "@/components/RevenueMovementChart";
import { formatMoney, formatMoment, humanize } from "@/components/dashboard-ui";
import {
  fetchJson,
  type BusinessOverview,
  type PaymentList,
  type RevenueSeries,
} from "@/lib/api/merchant-contracts";

type HomeData = {
  overview: BusinessOverview;
  series: RevenueSeries;
  payments: PaymentList;
};

export default function DashboardHome() {
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    let active = true;
    Promise.all([
      fetchJson<BusinessOverview>(`/api/v1/dashboard/business-overview?days=${days}`),
      fetchJson<RevenueSeries>(`/api/v1/dashboard/revenue-series?days=${days}`),
      fetchJson<PaymentList>("/api/v1/dashboard/payments?limit=5"),
    ])
      .then(([overview, series, payments]) => {
        if (active) setData({ overview, series, payments });
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Dashboard unavailable");
      });
    return () => {
      active = false;
    };
  }, [days]);

  if (error) return <DataState title="Authoritative data is unavailable" detail={error} />;
  if (!data) return <DataState title="Loading merchant activity" detail="Reading PostgreSQL…" />;

  const primary = data.overview.currency_totals[0];
  const totalOutcomes = primary
    ? primary.successful_payment_count + primary.failed_payment_count
    : 0;
  const successPercentage = primary ? primary.success_rate_basis_points / 100 : 0;
  const failedPercentage = totalOutcomes
    ? ((primary?.failed_payment_count ?? 0) * 100) / totalOutcomes
    : 0;
  const recoveredPercentage = primary?.gross_volume_minor
    ? Math.min(100, (primary.verified_recovered_minor * 100) / primary.gross_volume_minor)
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between border-b border-gray-200 pb-4">
        <div>
          <h1 className="text-3xl font-heading text-ledger-navy font-medium tracking-tight">
            {data.overview.context.merchant_display_name}
          </h1>
          <div className="flex items-center gap-2 mt-2" aria-label="Reporting period">
            {[1, 7, 30].map((period) => (
              <button
                className={
                  days === period
                    ? "rounded-full bg-payment-blue px-3 py-1 text-xs font-medium text-white"
                    : "rounded-full px-3 py-1 text-xs font-medium text-gray-500 hover:bg-gray-100"
                }
                key={period}
                onClick={() => {
                  setError(null);
                  setData(null);
                  setDays(period);
                }}
                type="button"
              >
                {period === 1 ? "Today" : `${period}d`}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 bg-blue-50 text-payment-blue rounded-md text-xs font-mono font-semibold uppercase tracking-wider">
          <span className="w-2 h-2 rounded-full bg-payment-blue" /> Test Mode
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Gross payment value"
          total={primary?.gross_volume_minor}
          currency={primary?.currency}
        />
        <MetricCard
          label="Successfully collected"
          total={primary?.collected_minor}
          currency={primary?.currency}
        />
        <MetricCard label="Success rate" percentage={successPercentage} />
        <MetricCard
          label="Failed payment value"
          total={primary?.failed_value_minor}
          currency={primary?.currency}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2 bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
          <h2 className="text-lg font-heading font-medium mb-1">Revenue movement</h2>
          <p className="text-sm text-gray-500 mb-6">Successful and failed payment value.</p>
          <div className="h-64">
            <RevenueMovementChart
              data={data.series.points
                .filter((point) => !primary || point.currency === primary.currency)
                .map((point) => ({
                  date: point.occurred_on,
                  collected: point.collected_minor / 100,
                  failed: point.failed_minor / 100,
                }))}
            />
          </div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
          <h2 className="text-lg font-heading font-medium mb-6">Payment methods</h2>
          {data.overview.payment_methods.length ? (
            <ul className="space-y-4">
              {data.overview.payment_methods.map((method) => (
                <li key={method.payment_method} className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-700">
                    {humanize(method.payment_method)}
                  </span>
                  <span className="text-sm font-mono text-gray-500">
                    {(method.share_basis_points / 100).toFixed(1)}%
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-500">No method observations in this period.</p>
          )}
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="px-6 py-5 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-lg font-heading font-medium">Recent payments</h2>
          <Link
            href="/dashboard/payments"
            className="text-sm text-payment-blue font-medium hover:underline"
          >
            View all
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-6 py-3">Reference</th>
                <th className="px-6 py-3">Customer</th>
                <th className="px-6 py-3">Method</th>
                <th className="px-6 py-3 text-right">Amount</th>
                <th className="px-6 py-3">Payment</th>
                <th className="px-6 py-3">Recovery</th>
                <th className="px-6 py-3">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.payments.payments.map((payment) => (
                <tr key={payment.payment_id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 font-mono text-gray-600">
                    {payment.provider_reference_masked}
                    {payment.classification === "SYNTHETIC" ? <SyntheticBadge /> : null}
                  </td>
                  <td className="px-6 py-4">{payment.customer_reference_masked ?? "—"}</td>
                  <td className="px-6 py-4">
                    {payment.payment_method ? humanize(payment.payment_method) : "—"}
                  </td>
                  <td className="px-6 py-4 text-right font-mono">
                    {formatMoney(payment.amount_minor, payment.currency)}
                  </td>
                  <td className="px-6 py-4">
                    <Status value={payment.status} />
                  </td>
                  <td className="px-6 py-4">
                    {payment.recovery_state ? humanize(payment.recovery_state) : "Not active"}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-gray-500">
                    {formatMoment(payment.occurred_at)}
                  </td>
                </tr>
              ))}
              {!data.payments.payments.length ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-gray-500">
                    No payments in this period.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h2 className="text-lg font-heading font-medium text-ledger-navy">Revenue recovery</h2>
            <p className="text-sm text-gray-500 mt-1">
              Verified recovered:{" "}
              {primary ? formatMoney(primary.verified_recovered_minor, primary.currency) : "—"}.
              Synthetic simulations are excluded.
            </p>
          </div>
          <Link
            href="/dashboard/recovery"
            className="flex items-center gap-2 text-sm text-payment-blue font-medium hover:underline"
          >
            View report <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
        <ProportionalBar
          successPercentage={successPercentage}
          failedPercentage={failedPercentage}
          recoveredPercentage={recoveredPercentage}
        />
      </div>
    </div>
  );
}

function MetricCard({
  label,
  total,
  currency,
  percentage,
}: {
  label: string;
  total?: number;
  currency?: string;
  percentage?: number;
}) {
  const value =
    percentage !== undefined
      ? `${percentage.toFixed(1)}%`
      : total !== undefined && currency
        ? formatMoney(total, currency)
        : "—";
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm flex flex-col">
      <span className="text-sm text-gray-500 font-medium">{label}</span>
      <span className="text-3xl font-mono font-medium text-gray-900 mt-2 tracking-tight">
        {value}
      </span>
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

function Status({ value }: { value: string }) {
  const success = ["captured", "paid", "success", "succeeded"].includes(value.toLowerCase());
  return (
    <span className={success ? "text-verified-green" : "text-critical-red"}>{humanize(value)}</span>
  );
}

function SyntheticBadge() {
  return (
    <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">
      SYNTHETIC
    </span>
  );
}
