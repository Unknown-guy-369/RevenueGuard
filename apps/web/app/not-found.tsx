import Link from "next/link";

export default function NotFound() {
  return (
    <main className="error-page">
      <span className="badge">NOT FOUND</span>
      <h1>This control surface does not exist.</h1>
      <p>Return to the RevenueGuard system overview.</p>
      <Link className="button button-primary" href="/">
        Open overview
      </Link>
    </main>
  );
}
