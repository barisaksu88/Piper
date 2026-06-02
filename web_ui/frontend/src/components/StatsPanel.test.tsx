import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import StatsPanel from "./StatsPanel";

describe("StatsPanel", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("renders overview cards when data is present", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "2 turns",
            recordCount: 2,
            turnNumbers: [1, 2],
            turnLabels: ["12:00:00", "12:00:05"],
            totalMs: [100, 300],
            routeMs: [10, 20],
            managerMs: [5, 10],
            reporterMs: [0, 0],
            personaMs: [80, 260],
            ttsMs: [0, 0],
            plannerTotalMs: [15, 30],
            executorTotalMs: [0, 0],
            alerts: [],
            recentTurns: [
              { timestamp: "2025-01-01T12:00:00", decision: "CHAT", outcome: "VERIFIED", totalMs: 100 },
              { timestamp: "2025-01-01T12:00:05", decision: "SEARCH", outcome: "VERIFIED", totalMs: 300 },
            ],
            receivedAt: Date.now(),
          }}
        />
      );
    });
    expect(container.textContent).toContain("Total turns");
    expect(container.textContent).toContain("2");
    expect(container.textContent).toContain("Avg latency");
    expect(container.textContent).toContain("200ms");
    expect(container.textContent).toContain("Latest latency");
    expect(container.textContent).toContain("300ms");
    expect(container.textContent).toContain("P95 latency");
  });

  it("renders alerts as a list", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "",
            recordCount: 1,
            turnNumbers: [1],
            turnLabels: ["12:00:00"],
            totalMs: [200],
            routeMs: [10],
            managerMs: [5],
            reporterMs: [0],
            personaMs: [180],
            ttsMs: [0],
            plannerTotalMs: [15],
            executorTotalMs: [0],
            alerts: ["High latency detected"],
            recentTurns: [
              { timestamp: "2025-01-01T12:00:00", decision: "CHAT", outcome: "VERIFIED", totalMs: 200 },
            ],
            receivedAt: Date.now(),
          }}
        />
      );
    });
    expect(container.textContent).toContain("Alerts (1)");
    expect(container.textContent).toContain("High latency detected");
  });

  it("renders recent turns as rows", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "",
            recordCount: 2,
            turnNumbers: [1, 2],
            turnLabels: ["12:00:00", "12:00:05"],
            totalMs: [100, 300],
            routeMs: [10, 20],
            managerMs: [5, 10],
            reporterMs: [0, 0],
            personaMs: [80, 260],
            ttsMs: [0, 0],
            plannerTotalMs: [15, 30],
            executorTotalMs: [0, 0],
            alerts: [],
            recentTurns: [
              { timestamp: "2025-01-01T12:00:00", decision: "CHAT", outcome: "VERIFIED", totalMs: 100 },
              { timestamp: "2025-01-01T12:00:05", decision: "SEARCH", outcome: "VERIFIED", totalMs: 300 },
            ],
            receivedAt: Date.now(),
          }}
        />
      );
    });
    expect(container.textContent).toContain("Recent turns");
    expect(container.textContent).toContain("CHAT");
    expect(container.textContent).toContain("SEARCH");
    expect(container.textContent).toContain("100ms");
    expect(container.textContent).toContain("300ms");
  });

  it("renders latency sparkline when enough data", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "",
            recordCount: 3,
            turnNumbers: [1, 2, 3],
            turnLabels: ["12:00:00", "12:00:05", "12:00:10"],
            totalMs: [100, 200, 150],
            routeMs: [10, 20, 15],
            managerMs: [5, 10, 8],
            reporterMs: [0, 0, 0],
            personaMs: [80, 160, 120],
            ttsMs: [0, 0, 0],
            plannerTotalMs: [15, 30, 23],
            executorTotalMs: [0, 0, 0],
            alerts: [],
            recentTurns: [
              { timestamp: "2025-01-01T12:00:00", decision: "CHAT", outcome: "VERIFIED", totalMs: 100 },
              { timestamp: "2025-01-01T12:00:05", decision: "CHAT", outcome: "VERIFIED", totalMs: 200 },
              { timestamp: "2025-01-01T12:00:10", decision: "CHAT", outcome: "VERIFIED", totalMs: 150 },
            ],
            receivedAt: Date.now(),
          }}
        />
      );
    });
    expect(container.querySelector(".stats-sparkline")).toBeTruthy();
  });

  it("renders empty state when no records", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "",
            recordCount: 0,
            turnNumbers: [],
            turnLabels: [],
            totalMs: [],
            routeMs: [],
            managerMs: [],
            reporterMs: [],
            personaMs: [],
            ttsMs: [],
            plannerTotalMs: [],
            executorTotalMs: [],
            alerts: [],
            recentTurns: [],
            receivedAt: null,
          }}
        />
      );
    });
    expect(container.textContent).toContain("No stats recorded yet");
  });
});
