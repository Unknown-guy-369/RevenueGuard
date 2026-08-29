"use client";

import { use, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Shield } from "lucide-react";

import { formatMoney, humanize } from "@/components/dashboard-ui";
import { fetchJson, type SimulationSession } from "@/lib/api/merchant-contracts";

type AttemptResult = {
  simulation_id: string;
  status: string;
  classification: "SYNTHETIC";
  provider_event_id: string;
};

export default function CheckoutPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = use(params);
  const [session, setSession] = useState<SimulationSession | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "submitting" | "submitted" | "error">(
    "loading",
  );
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchJson<SimulationSession>(`/api/v1/public/simulations/${encodeURIComponent(sessionId)}`)
      .then((value) => {
        if (active) {
          setSession(value);
          setStatus(value.status === "CREATED" ? "ready" : "submitted");
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setStatus("error");
          setMessage(reason instanceof Error ? reason.message : "Checkout unavailable");
        }
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  async function submitPayment() {
    setStatus("submitting");
    setMessage(null);
    try {
      const result = await fetchJson<AttemptResult>(
        `/api/v1/public/simulations/${encodeURIComponent(sessionId)}/attempt`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      setStatus("submitted");
      setMessage(
        `Synthetic provider event ${result.provider_event_id} was accepted for asynchronous processing.`,
      );
    } catch (reason) {
      setStatus("error");
      setMessage(
        reason instanceof Error ? reason.message : "The synthetic payment could not be submitted",
      );
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-gray-50 p-4">
      <div className="w-full max-w-md overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lg">
        <header className="bg-gray-900 p-6 text-center text-white">
          <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-white/10">
            <span className="font-bold">RG</span>
          </div>
          <h1 className="text-xl font-medium">
            {session?.merchant_display_name ?? "RevenueGuard Demo"}
          </h1>
          <p className="mt-1 text-sm text-white/70">Synthetic Test Mode checkout</p>
        </header>
        <div className="p-6">
          {session ? (
            <div className="mb-8 flex items-center justify-between border-b border-gray-100 pb-4">
              <div>
                <span className="font-medium text-gray-500">Amount to pay</span>
                <p className="mt-1 text-xs text-gray-400">
                  {humanize(session.flow_type)} · {humanize(session.scenario)}
                </p>
              </div>
              <span className="text-2xl font-semibold text-gray-900">
                {formatMoney(session.amount_minor, session.currency)}
              </span>
            </div>
          ) : null}

          {status === "loading" ? (
            <p className="py-10 text-center text-gray-500">Loading durable simulation session…</p>
          ) : null}
          {status === "ready" ? (
            <div className="space-y-5">
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900">
                <strong>No payment details are collected.</strong>
                <p className="mt-1 text-blue-800">
                  Submitting generates a deterministic Razorpay-shaped webhook and sends it through
                  RevenueGuard’s normal ingestion pipeline.
                </p>
              </div>
              <button
                onClick={() => void submitPayment()}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#3366FF] py-3 font-medium text-white transition-colors hover:bg-blue-600"
                type="button"
              >
                <Shield className="h-5 w-5" />
                Submit synthetic payment
              </button>
            </div>
          ) : null}
          {status === "submitting" ? (
            <div className="py-10 text-center">
              <span className="mx-auto block h-8 w-8 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600" />
              <p className="mt-4 text-sm text-gray-500">Persisting the signed synthetic event…</p>
            </div>
          ) : null}
          {status === "submitted" ? (
            <div className="py-8 text-center">
              <CheckCircle2 className="mx-auto h-14 w-14 text-verified-green" />
              <h2 className="mt-4 text-xl font-medium text-gray-900">Submitted for processing</h2>
              <p className="mt-2 text-sm text-gray-500">
                {message ?? "Return to the Simulation Lab for the authoritative event timeline."}
              </p>
            </div>
          ) : null}
          {status === "error" ? (
            <div role="alert" className="py-8 text-center">
              <AlertTriangle className="mx-auto h-14 w-14 text-critical-red" />
              <h2 className="mt-4 text-xl font-medium text-gray-900">Checkout unavailable</h2>
              <p className="mt-2 text-sm text-gray-500">{message}</p>
            </div>
          ) : null}
        </div>
        <footer className="flex items-center justify-center gap-2 border-t border-gray-100 bg-gray-50 p-4 text-xs text-gray-500">
          <Shield className="h-4 w-4" />
          Razorpay-shaped simulator · no real payment is attempted
        </footer>
      </div>
    </main>
  );
}
