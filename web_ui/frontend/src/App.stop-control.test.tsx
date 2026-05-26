import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const bridgeMock = vi.hoisted(() => {
  const sendAction = vi.fn();
  class FakeBridge {
    static lastInstance: FakeBridge | null = null;
    onStateChange?: (state: "disconnected" | "connecting" | "connected" | "error") => void;
    onFrame?: (frame: { frame: "event"; kind: string; payload: Record<string, unknown> }) => void;

    constructor(callbacks: {
      onStateChange?: (state: "disconnected" | "connecting" | "connected" | "error") => void;
      onFrame?: (frame: { frame: "event"; kind: string; payload: Record<string, unknown> }) => void;
    }) {
      this.onStateChange = callbacks.onStateChange;
      this.onFrame = callbacks.onFrame;
      bridgeMock.FakeBridge.lastInstance = this;
    }

    connect() {
      this.onStateChange?.("connected");
    }

    disconnect() {
      this.onStateChange?.("disconnected");
    }

    sendAction(action: string) {
      sendAction(action);
      return true;
    }

    emitFrame(kind: string, payload: Record<string, unknown> = {}) {
      this.onFrame?.({ frame: "event", kind, payload });
    }
  }
  return { FakeBridge, sendAction };
});

vi.mock("./bridge", () => ({
  PiperBridge: bridgeMock.FakeBridge,
  WS_URL: "ws://127.0.0.1:8787/ws",
}));

vi.mock("./hooks/useMic", () => ({
  useMic: () => ({
    micState: "idle",
    startMicRecording: vi.fn(),
    stopMicRecording: vi.fn(),
    abortMicRecording: vi.fn(),
    handleBackendMicStatus: vi.fn(),
    micButtonLabel: "MIC",
    micButtonClass: "",
    micStatusText: "",
  }),
}));

describe("App stop control gating", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    bridgeMock.sendAction.mockClear();
    bridgeMock.FakeBridge.lastInstance = null;
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

  it("keeps stop disabled while idle and does not send stop", async () => {
    await act(async () => {
      root.render(<App />);
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const stopButton = container.querySelector('button[title="Stop"]') as HTMLButtonElement | null;
    expect(stopButton).toBeTruthy();
    expect(stopButton!.disabled).toBe(true);

    await act(async () => {
      stopButton!.click();
    });

    expect(bridgeMock.sendAction).not.toHaveBeenCalledWith("stop");
  });

  it("sends stop once during active generation and stays gated after settle", async () => {
    await act(async () => {
      root.render(<App />);
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
      bridgeMock.FakeBridge.lastInstance!.emitFrame("stream.start");
      bridgeMock.FakeBridge.lastInstance!.emitFrame("stream.delta", { text: "Hello" });
    });

    const stopButton = container.querySelector('button[title="Stop"]') as HTMLButtonElement | null;
    expect(stopButton).toBeTruthy();
    expect(stopButton!.disabled).toBe(false);

    await act(async () => {
      stopButton!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledTimes(1);
    expect(bridgeMock.sendAction).toHaveBeenCalledWith("stop");
    expect(stopButton!.disabled).toBe(true);

    await act(async () => {
      stopButton!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledTimes(1);
  });
});
