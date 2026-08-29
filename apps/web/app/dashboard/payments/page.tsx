"use client";

import { useEffect, useState } from "react";
import { ChevronRight, Search, X } from "lucide-react";
import Link from "next/link";

import { formatMoney, formatMoment, humanize } from "@/components/dashboard-ui";
import {
  fetchJson,
  type PaymentDetail,
  type PaymentList,
  type PaymentSummary,
} from "@/lib/api/merchant-contracts";

const pageSize = 25;

export default function PaymentsPage() {
  const [result, setResult] = useState<PaymentList | null>(null);
  const [selected, setSelected] = useState<PaymentDetail | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    if (appliedQuery) params.set("query", appliedQuery);
    if (status) params.append("status", status);
    fetchJson<PaymentList>(`/api/v1/dashboard/payments?${params}`)
      .then((value) => {
        if (active) setResult(value);
      })
      .catch((reason: unknown) => {
        if (active)
          setError(reason instanceof Error ? reason.message : "Payment ledger unavailable");
      });
    return () => {
      active = false;
    };
  }, [appliedQuery, offset, status]);

  async function openPayment(payment: PaymentSummary) {
    try {
      setSelected(
        await fetchJson<PaymentDetail>(
          `/api/v1/dashboard/payments/${encodeURIComponent(payment.payment_id)}`,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Payment detail unavailable");
    }
  }

  const first = result && result.total ? result.offset + 1 : 0;
  const last = result ? Math.min(result.total, result.offset + result.payments.length) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-heading text-ledger-navy font-medium tracking-tight">
          Payments
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Successful, failed, and synthetic Test Mode payment activity.
        </p>
      </div>

      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          {error}
        </div>
      ) : null}

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <form
          className="p-4 border-b border-gray-200 flex flex-wrap items-center gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setOffset(0);
            setAppliedQuery(query.trim());
          }}
        >
          <div className="relative min-w-64 flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              aria-label="Search payments"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search reference or customer"
              className="w-full pl-9 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-payment-blue"
            />
          </div>
          <select
            aria-label="Payment status"
            className="text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">All statuses</option>
            <option value="CAPTURED">Successful</option>
            <option value="FAILED">Failed</option>
            <option value="AUTHORIZED">Authorized</option>
          </select>
          <button
            type="submit"
            className="rounded-lg bg-payment-blue px-4 py-2 text-sm font-medium text-white"
          >
            Search
          </button>
        </form>

        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wider">
              <tr>
                <th className="px-6 py-4">Reference</th>
                <th className="px-6 py-4">Customer</th>
                <th className="px-6 py-4 text-right">Amount</th>
                <th className="px-6 py-4">Method</th>
                <th className="px-6 py-4">Payment</th>
                <th className="px-6 py-4">Recovery</th>
                <th className="px-6 py-4">Time</th>
                <th className="px-6 py-4">
                  <span className="sr-only">Details</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {result?.payments.map((payment) => (
                <tr key={payment.payment_id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 font-mono text-payment-blue">
                    {payment.provider_reference_masked}
                    {payment.classification === "SYNTHETIC" ? (
                      <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-800">
                        SYNTHETIC
                      </span>
                    ) : null}
                  </td>
                  <td className="px-6 py-4">{payment.customer_reference_masked ?? "—"}</td>
                  <td className="px-6 py-4 text-right font-mono">
                    {formatMoney(payment.amount_minor, payment.currency)}
                  </td>
                  <td className="px-6 py-4 text-gray-600">
                    {payment.payment_method ? humanize(payment.payment_method) : "—"}
                  </td>
                  <td className="px-6 py-4">{humanize(payment.status)}</td>
                  <td className="px-6 py-4">
                    {payment.recovery_state ? humanize(payment.recovery_state) : "Not active"}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-gray-500">
                    {formatMoment(payment.occurred_at)}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button
                      type="button"
                      aria-label={`View ${payment.provider_reference_masked}`}
                      onClick={() => void openPayment(payment)}
                      className="rounded p-2 text-gray-400 hover:text-payment-blue"
                    >
                      <ChevronRight className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {result && !result.payments.length ? (
                <tr>
                  <td colSpan={8} className="px-6 py-16 text-center text-gray-500">
                    No payments match these filters.
                  </td>
                </tr>
              ) : null}
              {!result ? (
                <tr>
                  <td colSpan={8} className="px-6 py-16 text-center text-gray-500">
                    Loading payments…
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div className="p-4 border-t border-gray-200 flex items-center justify-between text-sm text-gray-500">
          <span>
            Showing {first}–{last} of {result?.total ?? 0}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - pageSize))}
              className="px-3 py-1 border rounded disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!result || offset + pageSize >= result.total}
              onClick={() => setOffset(offset + pageSize)}
              className="px-3 py-1 border rounded disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {selected ? <PaymentDrawer detail={selected} close={() => setSelected(null)} /> : null}
    </div>
  );
}

function PaymentDrawer({ detail, close }: { detail: PaymentDetail; close: () => void }) {
  const payment = detail.payment;
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="Payment details"
    >
      <button
        type="button"
        aria-label="Close payment details"
        className="absolute inset-0 bg-gray-900/20"
        onClick={close}
      />
      <div className="relative w-full max-w-xl bg-white h-full shadow-2xl flex flex-col">
        <div className="flex items-center justify-between p-6 border-b">
          <div>
            <h2 className="text-xl font-medium">Payment details</h2>
            <p className="text-sm font-mono text-gray-500 mt-1">
              {payment.provider_reference_masked}
            </p>
          </div>
          <button
            type="button"
            onClick={close}
            className="p-2 rounded-full hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-6 space-y-8">
          {payment.classification === "SYNTHETIC" ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              Synthetic Test Mode record. Excluded from merchant financial totals.
            </div>
          ) : null}
          <dl className="grid grid-cols-2 gap-5 text-sm">
            <div>
              <dt className="text-gray-500">Amount</dt>
              <dd className="mt-1 font-mono">
                {formatMoney(payment.amount_minor, payment.currency)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Status</dt>
              <dd className="mt-1">{humanize(payment.status)}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Customer</dt>
              <dd className="mt-1 font-mono">{payment.customer_reference_masked ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Order</dt>
              <dd className="mt-1 font-mono">{detail.order_reference_masked ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Method</dt>
              <dd className="mt-1">
                {payment.payment_method ? humanize(payment.payment_method) : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Failure</dt>
              <dd className="mt-1">
                {payment.failure_category ? humanize(payment.failure_category) : "None"}
              </dd>
            </div>
          </dl>
          <section className="border-t pt-6">
            <h3 className="font-medium">Recovery intelligence</h3>
            {payment.recovery_case_id ? (
              <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm">
                <p>{detail.diagnosis ? humanize(detail.diagnosis) : "Diagnosis pending"}</p>
                <p className="mt-2 text-gray-600">
                  State: {humanize(payment.recovery_state ?? "DETECTED")}
                </p>
                <Link
                  className="mt-3 inline-block text-payment-blue hover:underline"
                  href={`/dashboard/recovery/cases/${payment.recovery_case_id}`}
                >
                  View recovery report →
                </Link>
              </div>
            ) : (
              <p className="mt-2 text-sm text-gray-500">
                No recovery case is associated with this payment.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
