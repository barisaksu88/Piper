import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
  FakeBridge,
  sendAction,
  renderApp,
  cleanupApp,
} from "./test/appTestHarness";
import type { AppHarness } from "./test/appTestHarness";

vi.mock("./bridge", async () => {
  const { FakeBridge } = await import("./test/appTestHarness");
  return { PiperBridge: FakeBridge, WS_URL: "ws://127.0.0.1:8787/ws" };
});

vi.mock("./hooks/useMic", async () => {
  const { micMock } = await import("./test/appTestHarness");
  return { useMic: () => micMock };
});

describe("App view switching", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("defaults to Chat view after boot.ready", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    expect(harness.container.querySelector(".chat-input")).toBeTruthy();
    expect(harness.container.querySelector(".stats-view-content")).toBeNull();
  });

  it("switches to Stats view when Stats tab is clicked", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const statsTab = Array.from(harness.container.querySelectorAll("button.nav-tab")).find(
      (b) => b.textContent === "Stats"
    ) as HTMLButtonElement | null;
    expect(statsTab).toBeTruthy();

    await act(async () => {
      statsTab!.click();
    });

    expect(harness.container.querySelector(".stats-view-content")).toBeTruthy();
    expect(harness.container.querySelector(".chat-input")).toBeNull();
  });

  it("switches back to Chat view when Chat tab is clicked", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const statsTab = Array.from(harness.container.querySelectorAll("button.nav-tab")).find(
      (b) => b.textContent === "Stats"
    ) as HTMLButtonElement;
    const chatTab = Array.from(harness.container.querySelectorAll("button.nav-tab")).find(
      (b) => b.textContent === "Chat"
    ) as HTMLButtonElement;

    await act(async () => {
      statsTab.click();
    });
    expect(harness.container.querySelector(".stats-view-content")).toBeTruthy();

    await act(async () => {
      chatTab.click();
    });
    expect(harness.container.querySelector(".chat-input")).toBeTruthy();
    expect(harness.container.querySelector(".stats-view-content")).toBeNull();
  });

  it("does not render separate Stats or Live Screen cards in right rail", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const railTitles = Array.from(harness.container.querySelectorAll(".rail-card-header h3")).map(
      (h) => h.textContent
    );
    expect(railTitles).not.toContain("Stats");
    expect(railTitles).not.toContain("Live Screen");
    expect(railTitles).toContain("Capture");
  });

  it("renders Capture card with live screen status in right rail", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("screen.refresh", { pending: true });
    });

    // Expand the Capture card
    const captureHeader = Array.from(harness.container.querySelectorAll(".rail-card-header")).find(
      (h) => h.textContent?.includes("Capture")
    ) as HTMLButtonElement | null;
    expect(captureHeader).toBeTruthy();

    await act(async () => {
      captureHeader!.click();
    });

    expect(harness.container.textContent).toContain("Starting...");
  });
});
