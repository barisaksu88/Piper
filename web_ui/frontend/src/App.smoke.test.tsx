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

describe("App smoke wiring", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("opens chat after boot.ready, shows backend mic status, and sends stop", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    expect(FakeBridge.lastInstance).toBeTruthy();
    expect(harness.container.textContent).toContain("Booting");
    expect(harness.container.querySelector(".chat-input")).toBeNull();

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const chatInput = harness.container.querySelector(".chat-input") as HTMLInputElement | null;
    expect(chatInput).toBeTruthy();
    expect(chatInput!.placeholder).toBe("Type a message...");

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("mic.status", {
        state: "listening",
        message: "Listening...",
      });
    });

    expect(harness.container.textContent).toContain("Listening...");

    const stopButton = harness.container.querySelector('button[title="Stop"]') as HTMLButtonElement | null;
    expect(stopButton).toBeTruthy();
    expect(stopButton!.disabled).toBe(true);

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("stream.start");
    });

    expect(stopButton!.disabled).toBe(false);

    await act(async () => {
      stopButton!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("stop", {});
  });
});
