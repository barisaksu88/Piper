import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TopBar from "./TopBar";

describe("TopBar", () => {
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

  it("renders Chat and Stats tabs", () => {
    act(() => {
      root.render(
        <TopBar
          connState="connected"
          statusText="Idle"
          modeText=""
          canStop={false}
          activeView="chat"
          onViewChange={vi.fn()}
          onNewSession={vi.fn()}
          onRestart={vi.fn()}
          onStop={vi.fn()}
          onOpenSystem={vi.fn()}
        />
      );
    });
    expect(container.textContent).toContain("Chat");
    expect(container.textContent).toContain("Stats");
  });

  it("marks Chat tab active when activeView is chat", () => {
    act(() => {
      root.render(
        <TopBar
          connState="connected"
          statusText="Idle"
          modeText=""
          canStop={false}
          activeView="chat"
          onViewChange={vi.fn()}
          onNewSession={vi.fn()}
          onRestart={vi.fn()}
          onStop={vi.fn()}
          onOpenSystem={vi.fn()}
        />
      );
    });
    const chatTab = container.querySelector("button.nav-tab.active");
    expect(chatTab?.textContent).toBe("Chat");
  });

  it("marks Stats tab active when activeView is stats", () => {
    act(() => {
      root.render(
        <TopBar
          connState="connected"
          statusText="Idle"
          modeText=""
          canStop={false}
          activeView="stats"
          onViewChange={vi.fn()}
          onNewSession={vi.fn()}
          onRestart={vi.fn()}
          onStop={vi.fn()}
          onOpenSystem={vi.fn()}
        />
      );
    });
    const activeTab = container.querySelector("button.nav-tab.active");
    expect(activeTab?.textContent).toBe("Stats");
  });

  it("calls onViewChange when Stats tab is clicked", () => {
    const onViewChange = vi.fn();
    act(() => {
      root.render(
        <TopBar
          connState="connected"
          statusText="Idle"
          modeText=""
          canStop={false}
          activeView="chat"
          onViewChange={onViewChange}
          onNewSession={vi.fn()}
          onRestart={vi.fn()}
          onStop={vi.fn()}
          onOpenSystem={vi.fn()}
        />
      );
    });
    const statsTab = Array.from(container.querySelectorAll("button.nav-tab")).find(
      (b) => b.textContent === "Stats"
    ) as HTMLButtonElement;
    expect(statsTab).toBeTruthy();
    act(() => {
      statsTab.click();
    });
    expect(onViewChange).toHaveBeenCalledWith("stats");
  });
});
