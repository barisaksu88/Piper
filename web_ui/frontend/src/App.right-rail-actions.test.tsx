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
    handleBackendMicStatus: vi.fn(),
    micButtonLabel: "MIC",
    micButtonClass: "",
    micStatusText: "",
  }),
}));

describe("App right rail capture action dispatch", () => {
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

  it("dispatches event_speech_mode, live_screen_mode, and live_screen_interval from Capture controls", async () => {
    await act(async () => {
      root.render(<App />);
    });

    expect(bridgeMock.FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const headers = Array.from(container.querySelectorAll(".rail-card-header"));
    const captureHeader = headers.find((h) => h.textContent?.includes("Capture")) as HTMLElement | undefined;
    expect(captureHeader).toBeTruthy();

    await act(async () => {
      captureHeader!.click();
    });

    const captureCard = captureHeader!.closest(".rail-card") as HTMLElement;
    expect(captureCard).toBeTruthy();

    const selects = Array.from(captureCard.querySelectorAll("select"));
    expect(selects.length).toBe(3);

    const [eventSpeechSelect, liveScreenSelect, intervalSelect] = selects as HTMLSelectElement[];

    expect(eventSpeechSelect.disabled).toBe(false);
    expect(liveScreenSelect.disabled).toBe(false);
    expect(intervalSelect.disabled).toBe(false);

    await act(async () => {
      eventSpeechSelect.value = "all";
      eventSpeechSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("event_speech_mode", { mode: "all" });

    await act(async () => {
      liveScreenSelect.value = "pointer";
      liveScreenSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("live_screen_mode", { mode: "pointer" });

    await act(async () => {
      intervalSelect.value = "5";
      intervalSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("live_screen_interval", { interval_s: 5 });
  });
});
