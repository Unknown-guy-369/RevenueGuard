import { ArchitectureCard } from "@/components/architecture-card";
import { StatusRail, type StatusStep } from "@/components/status-rail";

const controlSteps: StatusStep[] = [
  {
    label: "Event accepted",
    detail: "Signed input becomes a durable, deduplicated record.",
    state: "ready",
  },
  {
    label: "Decision bounded",
    detail: "An agent may recommend; deterministic policy authorizes.",
    state: "ready",
  },
  {
    label: "Action isolated",
    detail: "Only an idempotent outbox command reaches an executor.",
    state: "ready",
  },
  {
    label: "Outcome verified",
    detail: "Unknown stays unknown until provider evidence resolves it.",
    state: "ready",
  },
];

const architectureCards = [
  {
    eyebrow: "API",
    title: "FastAPI system boundary",
    description:
      "Typed liveness, readiness, and version contracts with PostgreSQL and Redis probes.",
    tag: "Operational",
  },
  {
    eyebrow: "WORKER",
    title: "Loss-aware queue worker",
    description:
      "JSON-only Celery tasks, late acknowledgement, and worker-loss rejection are the starting defaults.",
    tag: "Operational",
  },
  {
    eyebrow: "CONTRACTS",
    title: "Safety before runtime",
    description:
      "Five versioned domain schemas, six accepted ADRs, and frozen evaluation gates define the build.",
    tag: "Enforced",
  },
];

const dependencyRows = [
  ["PostgreSQL", "Authoritative financial and workflow state", "Required"],
  ["Redis", "Queue, cache, rate limits, and coordination only", "Required"],
  ["Razorpay", "Signed Test Mode ingestion and payment-link execution", "Enabled in Phase 4"],
  ["LLM", "Read-only bounded reasoning", "Disabled until Phase 5"],
] as const;

export default function Home() {
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <nav className="nav shell" aria-label="Primary navigation">
          <a className="wordmark" href="#top" aria-label="RevenueGuard home">
            <span className="wordmark-mark" aria-hidden="true">
              R
            </span>
            RevenueGuard
          </a>
          <div className="nav-links">
            <a href="#system">System</a>
            <a href="#boundaries">Boundaries</a>
            <a href="#verification">Verification</a>
          </div>
          <a className="button button-on-dark" href="#system">
            Inspect scaffold
          </a>
        </nav>

        <div id="top" className="hero-grid shell">
          <div className="hero-copy">
            <span className="badge badge-dark">PHASE 04 · SAFE EFFECTS</span>
            <h1 id="hero-title">A recovery system that refuses to guess.</h1>
            <p>
              RevenueGuard separates reasoning, authorization, execution, and verification so every
              financial claim has evidence behind it.
            </p>
            <div className="hero-actions">
              <a className="button button-primary" href="#system">
                View system
              </a>
              <a className="button button-secondary-dark" href="#verification">
                See test gates
              </a>
            </div>
          </div>

          <div className="hero-product" aria-label="Recovery control flow preview">
            <div className="control-card control-card-back" aria-hidden="true">
              <span>OUTCOME</span>
              <strong>UNKNOWN</strong>
            </div>
            <div className="control-card control-card-front">
              <div className="control-card-heading">
                <div>
                  <span className="overline">CONTROL RAIL</span>
                  <h2>One safe path to recovery</h2>
                </div>
                <span className="live-dot">Contract</span>
              </div>
              <StatusRail steps={controlSteps} />
            </div>
          </div>
        </div>
      </section>

      <section id="system" className="section section-light" aria-labelledby="system-title">
        <div className="shell">
          <div className="section-heading">
            <span className="badge">SYSTEM FOUNDATION</span>
            <h2 id="system-title">Runtime pieces, without pretend outcomes.</h2>
            <p>
              Phase 4 connects deterministic policy to a transactional action outbox, bounded Test
              Mode execution, explicit unknown outcomes, and evidence-backed recovery accounting.
            </p>
          </div>
          <div className="card-grid">
            {architectureCards.map((card) => (
              <ArchitectureCard key={card.eyebrow} {...card} />
            ))}
          </div>
        </div>
      </section>

      <section id="boundaries" className="section section-soft" aria-labelledby="boundaries-title">
        <div className="shell split-layout">
          <div className="section-heading compact">
            <span className="badge">DEPENDENCY BOUNDARY</span>
            <h2 id="boundaries-title">Truth has one home.</h2>
            <p>
              PostgreSQL owns financial and workflow state. Everything else may accelerate work, but
              it cannot redefine truth.
            </p>
          </div>
          <div className="dependency-list" role="list" aria-label="System dependencies">
            {dependencyRows.map(([name, role, status]) => (
              <div className="dependency-row" role="listitem" key={name}>
                <span className="dependency-icon" aria-hidden="true">
                  {name.slice(0, 1)}
                </span>
                <div>
                  <strong>{name}</strong>
                  <p>{role}</p>
                </div>
                <span className="dependency-status">{status}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section
        id="verification"
        className="section verification"
        aria-labelledby="verification-title"
      >
        <div className="shell verification-grid">
          <div>
            <span className="badge badge-dark">RELEASE GATE</span>
            <h2 id="verification-title">Every task ends with proof.</h2>
          </div>
          <div className="verification-card">
            <p className="overline">PHASE 4 CHECK</p>
            <ul>
              <li>Format and lint both workspaces</li>
              <li>Type-check Python and TypeScript</li>
              <li>Run contract, API, worker, and UI tests</li>
              <li>Build the dashboard and validate containers</li>
              <li>Apply migrations and smoke-test readiness</li>
            </ul>
            <code>make check</code>
          </div>
        </div>
      </section>

      <footer className="footer shell">
        <div>
          <a className="wordmark" href="#top">
            <span className="wordmark-mark" aria-hidden="true">
              R
            </span>
            RevenueGuard
          </a>
          <p>Bounded recovery. Verified outcomes.</p>
        </div>
        <p>Phase 4 effect control plane · Test Mode and verified outcomes only</p>
      </footer>
    </main>
  );
}
