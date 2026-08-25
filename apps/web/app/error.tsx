"use client";

export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="error-page">
      <span className="badge">INTERFACE ERROR</span>
      <h1>The dashboard could not render.</h1>
      <p>Runtime recovery services are unaffected. Retry this view.</p>
      <button className="button button-primary" type="button" onClick={reset}>
        Retry dashboard
      </button>
    </main>
  );
}
