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

describe("App workspace action dispatch", () => {
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

  it("dispatches list_workspace_files on open and read_workspace_file on file click", async () => {
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

    expect(container.querySelector(".workspace-overlay-full")).toBeTruthy();
    expect(bridgeMock.sendAction).toHaveBeenCalledWith("list_workspace_files", {});

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "main.py", path: "C:/Projects/Piper/data/workspace/main.py", size: 1024 },
          { name: "notes.txt", path: "C:/Projects/Piper/data/workspace/notes.txt", size: 512 },
        ],
      });
    });

    const items = Array.from(container.querySelectorAll(".workspace-file-item"));
    expect(items.length).toBe(2);

    const mainPy = items.find((el) => el.textContent?.includes("main.py")) as HTMLElement | undefined;
    expect(mainPy).toBeTruthy();

    await act(async () => {
      mainPy!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/main.py",
    });
  });

  it("opens a Python file, emits contents, and dispatches code_run with full path", async () => {
    await act(async () => {
      root.render(<App />);
    });

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

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/main.py",
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("file.contents", {
        path: "C:/Projects/Piper/data/workspace/main.py",
        name: "main.py",
        content: 'print("hello")',
      });
    });

    const codeEditor = container.querySelector(".code-editor") as HTMLTextAreaElement | null;
    expect(codeEditor).toBeTruthy();
    expect(codeEditor!.value).toBe('print("hello")');

    const runBtn = container.querySelector(".action-btn.primary") as HTMLButtonElement | null;
    expect(runBtn).toBeTruthy();
    expect(runBtn!.textContent).toBe("Run");
    expect(runBtn!.disabled).toBe(false);

    await act(async () => {
      runBtn!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("code_run", {
      path: "C:/Projects/Piper/data/workspace/main.py",
      content: 'print("hello")',
    });
  });

  it("opens a text file, emits contents, edits, and dispatches save_workspace_file", async () => {
    await act(async () => {
      root.render(<App />);
    });

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
          { name: "notes.txt", path: "C:/Projects/Piper/data/workspace/notes.txt", size: 512 },
        ],
      });
    });

    const notesItem = container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(notesItem).toBeTruthy();
    expect(notesItem!.textContent).toContain("notes.txt");

    await act(async () => {
      notesItem!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/notes.txt",
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("file.contents", {
        path: "C:/Projects/Piper/data/workspace/notes.txt",
        name: "notes.txt",
        content: "Initial notes",
      });
    });

    const textarea = container.querySelector(".text-editor") as HTMLTextAreaElement | null;
    expect(textarea).toBeTruthy();
    expect(textarea!.value).toBe("Initial notes");

    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setValue?.call(textarea, "Updated notes");
      textarea!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const saveBtn = container.querySelector(".action-btn.primary") as HTMLButtonElement | null;
    expect(saveBtn).toBeTruthy();
    expect(saveBtn!.disabled).toBe(false);

    await act(async () => {
      saveBtn!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("save_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/notes.txt",
      content: "Updated notes",
    });
  });
});
