import type { StatsState } from "../types";

interface StatsPanelProps {
  stats: StatsState;
}

export default function StatsPanel({ stats }: StatsPanelProps) {
  const hasData = stats.recordCount > 0;

  return (
    <div className="stats-panel">
      {hasData ? (
        <>
          <div className="stats-summary">{stats.summaryText || "No summary"}</div>
          <div className="stats-row">
            <span className="stats-label">Turns</span>
            <span className="stats-value">{stats.recordCount}</span>
          </div>
          {stats.totalMs.length > 0 && (
            <div className="stats-row">
              <span className="stats-label">Avg latency</span>
              <span className="stats-value">
                {Math.round(
                  stats.totalMs.reduce((a, b) => a + b, 0) / stats.totalMs.length
                )}
                ms
              </span>
            </div>
          )}
          {stats.receivedAt && (
            <div className="stats-meta">
              Updated: {new Date(stats.receivedAt).toLocaleTimeString()}
            </div>
          )}
        </>
      ) : (
        <div className="stats-empty">No stats recorded yet</div>
      )}
    </div>
  );
}
