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

  it("renders stats when data is present", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "2 turns",
            recordCount: 2,
            turnNumbers: [1, 2],
            totalMs: [100, 300],
            receivedAt: Date.now(),
          }}
        />
      );
    });
    expect(container.textContent).toContain("2 turns");
    expect(container.textContent).toContain("Turns");
    expect(container.textContent).toContain("2");
    expect(container.textContent).toContain("Avg latency");
    expect(container.textContent).toContain("200ms");
  });

  it("renders empty state when no records", () => {
    act(() => {
      root.render(
        <StatsPanel
          stats={{
            summaryText: "",
            recordCount: 0,
            turnNumbers: [],
            totalMs: [],
            receivedAt: null,
          }}
        />
      );
    });
    expect(container.textContent).toContain("No stats recorded yet");
  });
});
