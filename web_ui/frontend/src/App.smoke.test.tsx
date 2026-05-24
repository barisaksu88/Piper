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
    onError?: (message: string) => void;

    constructor(callbacks: {
      onStateChange?: (state: "disconnected" | "connecting" | "connected" | "error") => void;
      onFrame?: (frame: { frame: "event"; kind: string; payload: Record<string, unknown> }) => void;
      onError?: (message: string) => void;
    }) {
      this.onStateChange = callbacks.onStateChange;
      this.onFrame = callbacks.onFrame;
      this.onError = callbacks.onError;
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
    micButtonLabel: "MIC",
    micButtonClass: "",
    micStatusText: "",
  }),
}));

describe("App smoke wiring", () => {
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

  it("opens chat after boot.ready, shows backend mic status, and sends stop", async () => {
    await act(async () => {
      root.render(<App />);
    });

    expect(bridgeMock.FakeBridge.lastInstance).toBeTruthy();
    expect(container.textContent).toContain("Booting");
    expect(container.querySelector(".chat-input")).toBeNull();

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const chatInput = container.querySelector(".chat-input") as HTMLInputElement | null;
    expect(chatInput).toBeTruthy();
    expect(chatInput!.placeholder).toBe("Type a message...");

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("mic.status", {
        state: "listening",
        message: "Listening...",
      });
    });

    expect(container.textContent).toContain("Listening...");

    const stopButton = container.querySelector('button[title="Stop"]') as HTMLButtonElement | null;
    expect(stopButton).toBeTruthy();
    expect(stopButton!.disabled).toBe(false);

    await act(async () => {
      stopButton!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("stop");
  });
});
