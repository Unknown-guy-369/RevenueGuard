"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, Copy, ExternalLink, Play } from "lucide-react";

import {
  fetchJson,
  type SimulationEvents,
  type SimulationSession,
} from "@/lib/api/merchant-contracts";

type Scenario =
  "SUCCESS" | "INSUFFICIENT_FUNDS" | "AUTHENTICATION_FAILURE" | "ISSUER_OUTAGE" | "TIMEOUT";
type FlowType = "ONE_TIME" | "SUBSCRIPTION";

export default function SimulatorPage() {
  const [scenario, setScenario] = useState<Scenario>("INSUFFICIENT_FUNDS");
  const [amount, setAmount] = useState("2499");
  const [flowType, setFlowType] = useState<FlowType>("ONE_TIME");
  const [session, setSession] = useState<SimulationSession | null>(null);
  const [timeline, setTimeline] = useState<SimulationEvents | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!session) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const value = await fetchJson<SimulationEvents>(
          `/api/v1/simulations/${encodeURIComponent(session!.simulation_id)}/events`,
        );
        if (!active) return;
        setTimeline(value);
        if (!["COMPLETED", "FAILED", "EXPIRED"].includes(value.status))
          timer = setTimeout(poll, 2_000);
      } catch (reason) {
        if (active)
          setError(reason instanceof Error ? reason.message : "Simulation timeline unavailable");
      }
    }
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [session]);

  async function createDemo() {
    const rupees = Number(amount);
    if (!Number.isFinite(rupees) || rupees <= 0 || !Number.isInteger(rupees * 100)) {
      setError("Enter a valid positive amount with at most two decimal places.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const created = await fetchJson<SimulationSession>("/api/v1/simulations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scenario,
          flow_type: flowType,
          amount_minor: Math.round(rupees * 100),
          currency: "INR",
        }),
      });
      setSession(created);
      setTimeline(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Simulation could not be created");
    } finally {
      setCreating(false);
    }
  }

  const checkoutUrl = session
    ? `${typeof window === "undefined" ? "" : window.location.origin}${session.checkout_path}`
    : null;
  const listening = timeline
    ? !["COMPLETED", "FAILED", "EXPIRED"].includes(timeline.status)
    : Boolean(session);

  return (
    <div className="max-w-5xl space-y-6">
      <header className="border-b border-gray-200 pb-4">
        <h1 className="text-3xl font-heading font-medium tracking-tight text-ledger-navy">
          Simulation Lab
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Generate deterministic, durable Razorpay-shaped Test Mode events.
        </p>
      </header>
      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          {error}
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-8 xl:grid-cols-5">
        <div className="space-y-6 xl:col-span-2">
          <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="mb-4 text-lg font-medium text-gray-900">Scenario configuration</h2>
            <div className="space-y-4">
              <label className="block text-sm font-medium text-gray-700">
                Payment scenario
                <select
                  className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                  value={scenario}
                  onChange={(event) => setScenario(event.target.value as Scenario)}
                >
                  <option value="SUCCESS">Successful payment</option>
                  <option value="INSUFFICIENT_FUNDS">Insufficient funds</option>
                  <option value="AUTHENTICATION_FAILURE">Authentication failure</option>
                  <option value="ISSUER_OUTAGE">Issuer outage</option>
                  <option value="TIMEOUT">Gateway timeout</option>
                </select>
              </label>
              <div className="grid grid-cols-2 gap-4">
                <label className="block text-sm font-medium text-gray-700">
                  Amount (INR)
                  <input
                    min="0.01"
                    step="0.01"
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                    type="number"
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                  />
                </label>
                <label className="block text-sm font-medium text-gray-700">
                  Flow
                  <select
                    className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                    value={flowType}
                    onChange={(event) => setFlowType(event.target.value as FlowType)}
                  >
                    <option value="ONE_TIME">One-time</option>
                    <option value="SUBSCRIPTION">Subscription</option>
                  </select>
                </label>
              </div>
              <button
                disabled={creating}
                onClick={() => void createDemo()}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-payment-blue px-4 py-2 font-medium text-white transition-colors hover:bg-payment-blue/90 disabled:opacity-50"
                type="button"
              >
                <Play className="h-4 w-4" />
                {creating ? "Creating…" : "Create demo checkout"}
              </button>
            </div>
          </section>
          {session && checkoutUrl ? (
            <section className="rounded-xl border border-payment-blue/30 bg-blue-50/20 p-6 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-ledger-navy">
                  Synthetic session
                </h2>
                <span className="rounded bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-900">
                  SYNTHETIC
                </span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-white p-3">
                <code className="mr-2 truncate text-xs text-gray-600">{checkoutUrl}</code>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    aria-label="Copy checkout URL"
                    onClick={() => void navigator.clipboard.writeText(checkoutUrl)}
                    className="rounded p-1.5 text-gray-400 hover:bg-blue-50 hover:text-payment-blue"
                    type="button"
                  >
                    <Copy className="h-4 w-4" />
                  </button>
                  <Link
                    aria-label="Open checkout"
                    href={session.checkout_path}
                    target="_blank"
                    className="rounded p-1.5 text-gray-400 hover:bg-blue-50 hover:text-payment-blue"
                  >
                    <ExternalLink className="h-4 w-4" />
                  </Link>
                </div>
              </div>
              <p className="mt-4 flex items-center gap-2 text-xs text-gray-500">
                <Activity
                  className={`h-4 w-4 text-payment-blue ${listening ? "animate-pulse" : ""}`}
                />
                {timeline?.status ?? session.status} · expires{" "}
                {new Date(session.expires_at).toLocaleString()}
              </p>
            </section>
          ) : null}
        </div>
        <section className="flex min-h-[500px] flex-col overflow-hidden rounded-xl border border-gray-800 bg-[#0a0b0d] shadow-xl xl:col-span-3">
          <div className="flex items-center justify-between border-b border-gray-800 bg-[#16181c] px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-gray-400">LIVE TIMELINE</span>
              <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest text-white">
                Synthetic
              </span>
            </div>
            {listening ? (
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 animate-pulse rounded-full bg-verified-green" />
                <span className="text-xs font-mono text-gray-400">Listening</span>
              </div>
            ) : null}
          </div>
          <div className="flex-1 overflow-y-auto p-4 font-mono text-sm text-gray-300">
            {timeline?.events.length ? (
              <ul className="space-y-3">
                {timeline.events.map((event) => (
                  <li className="flex items-start gap-4" key={event.event_id}>
                    <time className="shrink-0 text-gray-500">
                      {new Date(event.occurred_at).toLocaleTimeString()}
                    </time>
                    <span
                      className={
                        event.category === "ERROR"
                          ? "text-critical-red"
                          : event.category === "SUCCESS"
                            ? "text-verified-green"
                            : event.category === "WARNING"
                              ? "text-risk-amber"
                              : "text-gray-300"
                      }
                    >
                      {event.message}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="flex h-full items-center justify-center text-center text-gray-600">
                <p>
                  {session
                    ? "Open the checkout and submit the payment to populate the durable event timeline."
                    : "Create a demo checkout to start the simulation."}
                </p>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
