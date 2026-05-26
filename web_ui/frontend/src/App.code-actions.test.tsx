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

describe("App code active controls dispatch", () => {
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

  it("dispatches code_send on stdin Send and stop on Stop when process is active", async () => {
    await act(async () => {
      root.render(<App />);
    });

    expect(bridgeMock.FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "main.py", path: "C:/Projects/Piper/data/workspace/main.py", size: 1024 },
        ],
      });
    });

    const mainItem = container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(mainItem).toBeTruthy();
    expect(mainItem!.textContent).toContain("main.py");

    await act(async () => {
      mainItem!.click();
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("file.contents", {
        path: "C:/Projects/Piper/data/workspace/main.py",
        name: "main.py",
        content: "print('hello')",
      });
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("code.active", { active: true });
    });

    const stopBtn = container.querySelector(".code-controls .action-btn.primary") as HTMLButtonElement | null;
    expect(stopBtn).toBeTruthy();
    expect(stopBtn!.textContent).toBe("Stop");
    expect(stopBtn!.disabled).toBe(false);

    const stdinInput = container.querySelector(".code-stdin-row .input-text") as HTMLInputElement | null;
    expect(stdinInput).toBeTruthy();
    expect(stdinInput!.disabled).toBe(false);

    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setValue?.call(stdinInput, "hello stdin");
      stdinInput!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const sendBtn = container.querySelector(".code-stdin-row .action-btn") as HTMLButtonElement | null;
    expect(sendBtn).toBeTruthy();
    expect(sendBtn!.textContent).toBe("Send");
    expect(sendBtn!.disabled).toBe(false);

    await act(async () => {
      sendBtn!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("code_send", { text: "hello stdin" });

    await act(async () => {
      stopBtn!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("stop", {});
  });
});
