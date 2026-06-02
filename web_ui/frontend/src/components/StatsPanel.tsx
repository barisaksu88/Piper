import type { StatsState } from "../types";

interface StatsPanelProps {
  stats: StatsState;
}

export default function StatsPanel({ stats }: StatsPanelProps) {
  const hasData = stats.recordCount > 0;
  const avgLatency =
    stats.totalMs.length > 0
      ? Math.round(stats.totalMs.reduce((a, b) => a + b, 0) / stats.totalMs.length)
      : 0;

  return (
    <div className="stats-panel">
      {hasData ? (
        <>
          <div className="stats-summary">{stats.summaryText || "No summary"}</div>
          <div className="stats-grid">
            <div className="stats-cell">
              <span className="stats-cell-label">Turns</span>
              <span className="stats-cell-value">{stats.recordCount}</span>
            </div>
            <div className="stats-cell">
              <span className="stats-cell-label">Avg latency</span>
              <span className="stats-cell-value">{avgLatency}ms</span>
            </div>
            {stats.turnNumbers.length > 0 && (
              <div className="stats-cell">
                <span className="stats-cell-label">Latest turn</span>
                <span className="stats-cell-value">{stats.turnNumbers[stats.turnNumbers.length - 1]}</span>
              </div>
            )}
            {stats.totalMs.length > 0 && (
              <div className="stats-cell">
                <span className="stats-cell-label">Latest latency</span>
                <span className="stats-cell-value">{stats.totalMs[stats.totalMs.length - 1]}ms</span>
              </div>
            )}
          </div>
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
