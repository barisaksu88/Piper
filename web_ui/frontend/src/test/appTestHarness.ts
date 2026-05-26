import { vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

// --- Shared mocked bridge ---
export const sendAction = vi.fn();

export class FakeBridge {
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
    FakeBridge.lastInstance = this;
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

// --- Shared mocked useMic return ---
export const handleBackendMicStatus = vi.fn();

export const micMock = {
  micState: "idle" as const,
  startMicRecording: vi.fn(),
  stopMicRecording: vi.fn(),
  abortMicRecording: vi.fn(),
  handleBackendMicStatus,
  micButtonLabel: "MIC",
  micButtonClass: "",
  micStatusText: "",
};

// --- Render / cleanup helpers ---
export interface AppHarness {
  container: HTMLDivElement;
  root: Root;
}

export function renderApp(): AppHarness {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  return { container, root };
}

export function cleanupApp(harness: AppHarness) {
  act(() => {
    harness.root.unmount();
  });
  harness.container.remove();
}
