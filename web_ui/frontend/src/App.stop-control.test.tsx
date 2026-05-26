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

describe("App stop control gating", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("keeps stop disabled while idle and does not send stop", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const stopButton = harness.container.querySelector('button[title="Stop"]') as HTMLButtonElement | null;
    expect(stopButton).toBeTruthy();
    expect(stopButton!.disabled).toBe(true);

    await act(async () => {
      stopButton!.click();
    });

    expect(sendAction).not.toHaveBeenCalledWith("stop", {});
  });

  it("sends stop once during active generation and stays gated after settle", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
      FakeBridge.lastInstance!.emitFrame("stream.start");
      FakeBridge.lastInstance!.emitFrame("stream.delta", { text: "Hello" });
    });

    const stopButton = harness.container.querySelector('button[title="Stop"]') as HTMLButtonElement | null;
    expect(stopButton).toBeTruthy();
    expect(stopButton!.disabled).toBe(false);

    await act(async () => {
      stopButton!.click();
    });

    expect(sendAction).toHaveBeenCalledTimes(1);
    expect(sendAction).toHaveBeenCalledWith("stop", {});
    expect(stopButton!.disabled).toBe(true);

    await act(async () => {
      stopButton!.click();
    });

    expect(sendAction).toHaveBeenCalledTimes(1);
  });
});
