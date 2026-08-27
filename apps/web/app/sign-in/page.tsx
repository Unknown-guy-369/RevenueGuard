import { redirect } from "next/navigation";
import Link from "next/link";

import { dashboardAuthConfigured, getDashboardSession } from "@/lib/auth/session";

type SignInProps = {
  searchParams: Promise<{ error?: string }>;
};

export default async function SignIn({ searchParams }: SignInProps) {
  if ((await getDashboardSession()) !== null) redirect("/dashboard");
  const { error } = await searchParams;
  const configured = dashboardAuthConfigured();
  return (
    <main className="sign-in-page">
      <section className="sign-in-panel" aria-labelledby="sign-in-title">
        <Link className="wordmark" href="/">
          <span className="wordmark-mark" aria-hidden="true">
            R
          </span>
          RevenueGuard
        </Link>
        <div className="sign-in-copy">
          <span className="badge">OPERATOR ACCESS</span>
          <h1 id="sign-in-title">Open the recovery control room.</h1>
          <p>
            This workspace contains merchant-scoped Test Mode workflow evidence. Enter the operator
            key configured on this server.
          </p>
        </div>
        {!configured ? (
          <div className="notice notice-danger" role="alert">
            Dashboard authentication is not configured. Set the operator access key, merchant,
            session secret, and internal API token, then restart the web service.
          </div>
        ) : (
          <form className="sign-in-form" action="/api/session" method="post">
            <label htmlFor="access-key">Operator access key</label>
            <input
              id="access-key"
              name="access_key"
              type="password"
              autoComplete="current-password"
              required
              maxLength={512}
            />
            {error === "invalid" ? (
              <p className="field-error" role="alert">
                The operator key was not accepted.
              </p>
            ) : null}
            <button className="button button-primary" type="submit">
              Open dashboard
            </button>
          </form>
        )}
        <p className="sign-in-footnote">
          Credentials remain server-side and are never sent to the RevenueGuard model.
        </p>
      </section>
      <aside className="sign-in-aside" aria-label="RevenueGuard safety summary">
        <p className="overline">THE CONTROL RULE</p>
        <blockquote>Recommendation can be probabilistic. Authorization cannot.</blockquote>
        <div className="sign-in-rail">
          <span>01 · Evidence</span>
          <span>02 · Policy</span>
          <span>03 · Action</span>
          <span>04 · Verify</span>
        </div>
      </aside>
    </main>
  );
}
