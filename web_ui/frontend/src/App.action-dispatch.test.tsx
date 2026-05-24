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

    sendAction(action: string, payload: Record<string, unknown> = {}) {
      sendAction(action, payload);
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

describe("App action dispatch", () => {
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

  it("dispatches send_message, new_session, and restart_piper from visible controls", async () => {
    await act(async () => {
      root.render(<App />);
    });

    expect(bridgeMock.FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const chatInput = container.querySelector(".chat-input") as HTMLInputElement | null;
    const sendButton = container.querySelector(".send-btn") as HTMLButtonElement | null;
    const newSessionButton = container.querySelector('button[title="New Session"]') as HTMLButtonElement | null;
    const restartButton = container.querySelector('button[title="Restart"]') as HTMLButtonElement | null;

    expect(chatInput).toBeTruthy();
    expect(sendButton).toBeTruthy();
    expect(newSessionButton).toBeTruthy();
    expect(restartButton).toBeTruthy();
    expect(chatInput!.disabled).toBe(false);

    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setValue?.call(chatInput, "Hello Piper");
      chatInput!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(sendButton!.disabled).toBe(false);

    await act(async () => {
      sendButton!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("send_message", { text: "Hello Piper" });

    await act(async () => {
      newSessionButton!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("new_session", {});

    await act(async () => {
      restartButton!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("restart_piper", {});
  });
});
