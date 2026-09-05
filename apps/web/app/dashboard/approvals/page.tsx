"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, ExternalLink, X } from "lucide-react";

import { formatMoney, formatMoment, humanize } from "@/components/dashboard-ui";
import { fetchJson, type ReviewList } from "@/lib/api/merchant-contracts";

function isExpired(expiresAt: string, now: number): boolean {
  const expiry = Date.parse(expiresAt);
  return Number.isFinite(expiry) && expiry <= now;
}

export default function ApprovalsPage() {
  const [data, setData] = useState<ReviewList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyReview, setBusyReview] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const [pendingDecision, setPendingDecision] = useState<{
    reviewId: string;
    expiresAt: string;
    decision: "APPROVE" | "REJECT";
  } | null>(null);
  const [rationale, setRationale] = useState("");

  useEffect(() => {
    let active = true;
    fetchJson<ReviewList>("/api/v1/dashboard/reviews")
      .then((value) => {
        if (active) setData(value);
      })
      .catch((reason: unknown) => {
        if (active)
          setError(reason instanceof Error ? reason.message : "Approval queue unavailable");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const nextExpiry = data?.reviews
      .map((review) => Date.parse(review.expires_at))
      .filter((expiry) => Number.isFinite(expiry) && expiry > currentTime)
      .reduce<number | null>((earliest, expiry) => {
        if (earliest === null || expiry < earliest) return expiry;
        return earliest;
      }, null);
    if (nextExpiry === null || nextExpiry === undefined) return;

    const timer = window.setTimeout(
      () => setCurrentTime(Date.now()),
      Math.min(nextExpiry - currentTime, 2_147_483_647),
    );
    return () => window.clearTimeout(timer);
  }, [currentTime, data]);

  function openDecision(reviewId: string, expiresAt: string, decision: "APPROVE" | "REJECT") {
    if (isExpired(expiresAt, currentTime)) return;
    setRationale("");
    setError(null);
    setPendingDecision({ reviewId, expiresAt, decision });
  }

  async function submitDecision() {
    if (pendingDecision === null || rationale.trim().length < 3) return;
    if (isExpired(pendingDecision.expiresAt, currentTime)) {
      setError("This review expired before the decision could be recorded.");
      setPendingDecision(null);
      return;
    }
    setBusyReview(pendingDecision.reviewId);
    setError(null);
    try {
      await fetchJson(
        `/api/v1/dashboard/reviews/${encodeURIComponent(pendingDecision.reviewId)}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision: pendingDecision.decision,
            rationale: rationale.trim(),
          }),
        },
      );
      setData(await fetchJson<ReviewList>("/api/v1/dashboard/reviews"));
      setPendingDecision(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Decision could not be recorded");
    } finally {
      setBusyReview(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="border-b border-gray-200 pb-4">
        <h1 className="text-3xl font-heading font-medium tracking-tight text-ledger-navy">
          Human Approvals
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Review sensitive actions before policy and provider state are checked again.
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
      {!data && !error ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-gray-500">
          Loading authoritative review queue…
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        {data?.reviews.map((review) => {
          const expired = isExpired(review.expires_at, currentTime);
          return (
            <article
              className="flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
              key={review.review_id}
            >
              <div>
                <div className="mb-4 flex items-start justify-between gap-4">
                  <div>
                    <h2 className="font-mono text-sm font-medium text-gray-900">
                      {review.customer_reference_masked ?? "Customer reference unavailable"}
                    </h2>
                    <p className="mt-1 font-mono text-sm text-gray-500">
                      {formatMoney(review.amount_minor, review.currency)}
                    </p>
                  </div>
                  <span
                    className={
                      expired
                        ? "rounded border border-gray-300 bg-gray-100 px-2 py-1 text-xs font-semibold tracking-wide text-gray-700"
                        : "rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs font-semibold tracking-wide text-amber-800"
                    }
                  >
                    {expired ? "EXPIRED" : "PENDING"}
                  </span>
                  {review.classification === "SYNTHETIC" ? (
                    <span className="rounded bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-900">
                      SYNTHETIC
                    </span>
                  ) : null}
                </div>
                <dl className="mb-6 grid grid-cols-[auto_1fr] gap-x-3 gap-y-3 text-sm">
                  <dt className="font-medium text-gray-900">Proposed action</dt>
                  <dd className="text-gray-600">{humanize(review.proposed_action_type)}</dd>
                  <dt className="font-medium text-gray-900">Diagnosis</dt>
                  <dd className="text-gray-600">
                    {review.diagnosis ? humanize(review.diagnosis) : "Not available"}
                  </dd>
                  <dt className="font-medium text-gray-900">Confidence</dt>
                  <dd className="text-gray-600">
                    {review.confidence_basis_points === null
                      ? "Not available"
                      : `${(review.confidence_basis_points / 100).toFixed(1)}%`}
                  </dd>
                  <dt className="font-medium text-gray-900">Reason</dt>
                  <dd className="text-gray-600">
                    {humanize(review.reason_code)} · {review.risk_detail}
                  </dd>
                  <dt className="font-medium text-gray-900">Policy</dt>
                  <dd>
                    <code className="rounded bg-gray-100 px-1 text-gray-700">
                      {review.policy_version}
                    </code>
                  </dd>
                </dl>
                <p className="mb-4 text-xs text-gray-500">
                  Requested {formatMoment(review.requested_at)} · Expires{" "}
                  {formatMoment(review.expires_at)}
                </p>
              </div>
              <div className="flex items-center gap-3 border-t border-gray-100 pt-4">
                <button
                  disabled={expired || busyReview === review.review_id}
                  onClick={() => openDecision(review.review_id, review.expires_at, "APPROVE")}
                  className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-ledger-navy px-4 py-2 font-medium text-white transition-colors hover:bg-ledger-navy/90 disabled:opacity-50"
                  type="button"
                >
                  <Check className="h-4 w-4" />
                  Approve
                </button>
                <button
                  disabled={expired || busyReview === review.review_id}
                  onClick={() => openDecision(review.review_id, review.expires_at, "REJECT")}
                  className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-50"
                  type="button"
                >
                  <X className="h-4 w-4" />
                  Reject
                </button>
                <Link
                  aria-label="Open full recovery case"
                  className="rounded-lg bg-gray-50 p-2 text-gray-500 transition-colors hover:bg-blue-50 hover:text-payment-blue"
                  href={`/dashboard/recovery/cases/${review.case_id}`}
                >
                  <ExternalLink className="h-5 w-5" />
                </Link>
              </div>
            </article>
          );
        })}
        {data && !data.reviews.length ? (
          <div className="rounded-xl border border-gray-200 bg-white p-12 text-center shadow-sm xl:col-span-2">
            <Check className="mx-auto h-7 w-7 text-gray-400" />
            <h2 className="mt-4 text-lg font-medium text-gray-900">Queue empty</h2>
            <p className="mt-2 text-gray-500">
              There are no pending actions requiring merchant approval.
            </p>
          </div>
        ) : null}
      </div>
      {pendingDecision ? (
        <div
          aria-labelledby="decision-dialog-title"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ledger-navy/40 p-4"
          role="dialog"
        >
          <form
            className="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl"
            onSubmit={(event) => {
              event.preventDefault();
              void submitDecision();
            }}
          >
            <h2
              className="text-xl font-heading font-medium text-ledger-navy"
              id="decision-dialog-title"
            >
              {pendingDecision.decision === "APPROVE" ? "Approve" : "Reject"} recovery action
            </h2>
            <p className="mt-2 text-sm text-gray-600">
              Record a rationale for the audit trail. Policy and provider state are checked again
              before any action executes.
            </p>
            <label
              className="mt-5 block text-sm font-medium text-gray-800"
              htmlFor="review-rationale"
            >
              Rationale
            </label>
            <textarea
              autoFocus
              className="mt-2 min-h-28 w-full rounded-lg border border-gray-300 p-3 text-sm focus:border-payment-blue focus:outline-none focus:ring-1 focus:ring-payment-blue"
              id="review-rationale"
              minLength={3}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="Explain the evidence supporting this decision."
              required
              value={rationale}
            />
            <div className="mt-6 flex justify-end gap-3">
              <button
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                onClick={() => setPendingDecision(null)}
                type="button"
              >
                Cancel
              </button>
              <button
                className="rounded-lg bg-ledger-navy px-4 py-2 text-sm font-medium text-white hover:bg-ledger-navy/90 disabled:opacity-50"
                disabled={busyReview !== null || rationale.trim().length < 3}
                type="submit"
              >
                {busyReview ? "Recording…" : `Confirm ${pendingDecision.decision.toLowerCase()}`}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
