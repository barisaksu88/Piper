import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
  FakeBridge,
  sendAction,
  handleBackendMicStatus,
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

describe("App mic backend acknowledgement", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
    handleBackendMicStatus.mockClear();
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("forwards mic.status frames to useMic handleBackendMicStatus", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    expect(FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("mic.status", {
        state: "idle",
      });
    });

    expect(handleBackendMicStatus).toHaveBeenCalledWith({ state: "idle" });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("mic.status", {
        state: "error",
        error: "Backend mic failed",
      });
    });

    expect(handleBackendMicStatus).toHaveBeenCalledWith({
      state: "error",
      error: "Backend mic failed",
    });
  });
});
