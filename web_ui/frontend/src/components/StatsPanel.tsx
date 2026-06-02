import { memo } from "react";
import type { StatsState } from "../types";

interface StatsPanelProps {
  stats: StatsState;
}

function _p95(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.ceil(sorted.length * 0.95) - 1;
  return Math.round(sorted[Math.max(0, idx)]);
}

function Sparkline({ values, width = 300 }: { values: number[]; width?: number }) {
  if (values.length < 2) {
    return <div className="stats-chart-empty">Not enough data</div>;
  }
  const max = Math.max(...values, 1);
  const min = Math.min(...values);
  const range = max - min || 1;
  const pad = 4;
  const chartH = 60 - pad * 2;
  const chartW = width - pad * 2;
  const step = chartW / (values.length - 1);
  const points = values.map((v, i) => {
    const x = pad + i * step;
    const y = pad + chartH - ((v - min) / range) * chartH;
    return `${x},${y}`;
  });
  const polyline = points.join(" ");
  return (
    <svg className="stats-sparkline" viewBox={`0 0 ${width} 60`} width={width} height={60}>
      <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={polyline} />
      {values.map((v, i) => {
        const x = pad + i * step;
        const y = pad + chartH - ((v - min) / range) * chartH;
        return <circle key={i} cx={x} cy={y} r="2.5" fill="var(--accent)" />;
      })}
    </svg>
  );
}

function BarSeries({
  labels,
  values,
  width = 300,
}: {
  labels: string[];
  values: number[];
  width?: number;
}) {
  const data = labels.map((label, i) => ({ label, value: values[i] || 0 })).filter((d) => d.value > 0);
  if (data.length === 0) {
    return <div className="stats-chart-empty">No phase data</div>;
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  const barHeight = 16;
  const gap = 8;
  const pad = 6;
  const labelW = 64;
  const chartW = width - pad * 2 - labelW;
  const chartH = data.length * (barHeight + gap) + pad * 2;
  return (
    <svg className="stats-bar-series" viewBox={`0 0 ${width} ${chartH}`} width={width} height={chartH}>
      {data.map((d, i) => {
        const y = pad + i * (barHeight + gap);
        const barW = (d.value / max) * chartW;
        return (
          <g key={d.label}>
            <text x={pad} y={y + barHeight - 3} fontSize="10" fill="var(--text-dim)">
              {d.label}
            </text>
            <rect
              x={pad + labelW}
              y={y}
              width={barW}
              height={barHeight}
              rx="3"
              fill="var(--accent)"
              opacity="0.7"
            />
            <text
              x={pad + labelW + barW + 4}
              y={y + barHeight - 3}
              fontSize="10"
              fill="var(--text-dim)"
            >
              {Math.round(d.value)}ms
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function StatsPanel({ stats }: StatsPanelProps) {
  const hasData = stats.recordCount > 0;
  const avgLatency =
    stats.totalMs.length > 0
      ? Math.round(stats.totalMs.reduce((a, b) => a + b, 0) / stats.totalMs.length)
      : 0;
  const latestLatency = stats.totalMs.length > 0 ? Math.round(stats.totalMs[stats.totalMs.length - 1]) : 0;
  const p95Latency = _p95(stats.totalMs);

  const phaseLabels = ["route", "manager", "reporter", "persona", "tts"];
  const phaseValues = [
    stats.routeMs.length > 0 ? stats.routeMs.reduce((a, b) => a + b, 0) / stats.routeMs.length : 0,
    stats.managerMs.length > 0 ? stats.managerMs.reduce((a, b) => a + b, 0) / stats.managerMs.length : 0,
    stats.reporterMs.length > 0 ? stats.reporterMs.reduce((a, b) => a + b, 0) / stats.reporterMs.length : 0,
    stats.personaMs.length > 0 ? stats.personaMs.reduce((a, b) => a + b, 0) / stats.personaMs.length : 0,
    stats.ttsMs.length > 0 ? stats.ttsMs.reduce((a, b) => a + b, 0) / stats.ttsMs.length : 0,
  ];

  return (
    <div className="stats-panel">
      {hasData ? (
        <>
          {/* Overview cards */}
          <div className="stats-grid">
            <div className="stats-cell">
              <span className="stats-cell-label">Total turns</span>
              <span className="stats-cell-value">{stats.recordCount}</span>
            </div>
            <div className="stats-cell">
              <span className="stats-cell-label">Avg latency</span>
              <span className="stats-cell-value">{avgLatency}ms</span>
            </div>
            <div className="stats-cell">
              <span className="stats-cell-label">Latest latency</span>
              <span className="stats-cell-value">{latestLatency}ms</span>
            </div>
            <div className="stats-cell">
              <span className="stats-cell-label">P95 latency</span>
              <span className="stats-cell-value">{p95Latency}ms</span>
            </div>
          </div>

          {/* Latency sparkline */}
          {stats.totalMs.length > 1 && (
            <div className="stats-section">
              <h4 className="stats-section-title">Latency trend</h4>
              <Sparkline values={stats.totalMs} />
            </div>
          )}

          {/* Phase breakdown */}
          {phaseValues.some((v) => v > 0) && (
            <div className="stats-section">
              <h4 className="stats-section-title">Phase breakdown (avg)</h4>
              <BarSeries labels={phaseLabels} values={phaseValues} />
            </div>
          )}

          {/* Alerts */}
          {stats.alerts.length > 0 && (
            <div className="stats-section">
              <h4 className="stats-section-title">Alerts ({stats.alerts.length})</h4>
              <div className="stats-alert-list">
                {stats.alerts.map((a, i) => (
                  <div key={`alert-${i}`} className="stats-alert-item">
                    {a}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent turns */}
          {stats.recentTurns.length > 0 && (
            <div className="stats-section">
              <h4 className="stats-section-title">Recent turns</h4>
              <div className="stats-turns-table">
                <div className="stats-turns-header">
                  <span>Time</span>
                  <span>Decision</span>
                  <span>Outcome</span>
                  <span>Latency</span>
                </div>
                {stats.recentTurns.map((t, i) => (
                  <div key={`turn-${i}`} className="stats-turns-row">
                    <span>{t.timestamp ? t.timestamp.replace("T", " ").slice(0, 19) : "—"}</span>
                    <span>{t.decision}</span>
                    <span>{t.outcome}</span>
                    <span>{Math.round(t.totalMs)}ms</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Raw summary (collapsed, kept for reference) */}
          {stats.summaryText && (
            <details className="stats-raw-details">
              <summary>Raw summary</summary>
              <pre className="stats-raw-pre">{stats.summaryText}</pre>
            </details>
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

export default memo(StatsPanel);
