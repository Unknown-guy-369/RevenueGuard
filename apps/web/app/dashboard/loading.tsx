export default function DashboardLoading() {
  return (
    <main className="dashboard-shell loading-shell" aria-busy="true">
      <div className="dashboard-nav">
        <span className="wordmark">RevenueGuard</span>
      </div>
      <div className="dashboard-main">
        <div className="loading-line loading-title" />
        <div className="metric-strip">
          <div className="loading-card" />
          <div className="loading-card" />
          <div className="loading-card" />
        </div>
        <div className="loading-panel" />
      </div>
    </main>
  );
}
