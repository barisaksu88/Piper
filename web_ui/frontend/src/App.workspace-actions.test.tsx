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

describe("App workspace action dispatch", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("dispatches list_workspace_files on open and read_workspace_file on file click", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    expect(FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    expect(harness.container.querySelector(".workspace-overlay-full")).toBeTruthy();
    expect(sendAction).toHaveBeenCalledWith("list_workspace_files", {});

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "main.py", path: "C:/Projects/Piper/data/workspace/main.py", size: 1024 },
          { name: "notes.txt", path: "C:/Projects/Piper/data/workspace/notes.txt", size: 512 },
        ],
      });
    });

    const items = Array.from(harness.container.querySelectorAll(".workspace-file-item"));
    expect(items.length).toBe(2);

    const mainPy = items.find((el) => el.textContent?.includes("main.py")) as HTMLElement | undefined;
    expect(mainPy).toBeTruthy();

    await act(async () => {
      mainPy!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/main.py",
    });
  });

  it("opens a Python file, emits contents, and dispatches code_run with full path", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "main.py", path: "C:/Projects/Piper/data/workspace/main.py", size: 1024 },
        ],
      });
    });

    const mainItem = harness.container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(mainItem).toBeTruthy();
    expect(mainItem!.textContent).toContain("main.py");

    await act(async () => {
      mainItem!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/main.py",
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("file.contents", {
        path: "C:/Projects/Piper/data/workspace/main.py",
        name: "main.py",
        content: 'print("hello")',
      });
    });

    const codeEditor = harness.container.querySelector(".code-editor") as HTMLTextAreaElement | null;
    expect(codeEditor).toBeTruthy();
    expect(codeEditor!.value).toBe('print("hello")');

    const runBtn = harness.container.querySelector(".action-btn.primary") as HTMLButtonElement | null;
    expect(runBtn).toBeTruthy();
    expect(runBtn!.textContent).toBe("Run");
    expect(runBtn!.disabled).toBe(false);

    await act(async () => {
      runBtn!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("code_run", {
      path: "C:/Projects/Piper/data/workspace/main.py",
      content: 'print("hello")',
    });
  });

  it("opens a text file, emits contents, edits, and dispatches save_workspace_file", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "notes.txt", path: "C:/Projects/Piper/data/workspace/notes.txt", size: 512 },
        ],
      });
    });

    const notesItem = harness.container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(notesItem).toBeTruthy();
    expect(notesItem!.textContent).toContain("notes.txt");

    await act(async () => {
      notesItem!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/notes.txt",
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("file.contents", {
        path: "C:/Projects/Piper/data/workspace/notes.txt",
        name: "notes.txt",
        content: "Initial notes",
      });
    });

    const textarea = harness.container.querySelector(".text-editor") as HTMLTextAreaElement | null;
    expect(textarea).toBeTruthy();
    expect(textarea!.value).toBe("Initial notes");

    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setValue?.call(textarea, "Updated notes");
      textarea!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const saveBtn = harness.container.querySelector(".action-btn.primary") as HTMLButtonElement | null;
    expect(saveBtn).toBeTruthy();
    expect(saveBtn!.disabled).toBe(false);

    await act(async () => {
      saveBtn!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("save_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/notes.txt",
      content: "Updated notes",
    });
  });

  it("displays nested file workspace-relative labels and dispatches read with full path", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "main.py", path: "C:/Projects/Piper/data/workspace/src/main.py", size: 1024 },
          { name: "main.py", path: "C:/Projects/Piper/data/workspace/tests/main.py", size: 512 },
        ],
      });
    });

    expect(harness.container.textContent).toContain("src/main.py");
    expect(harness.container.textContent).toContain("tests/main.py");

    const items = Array.from(harness.container.querySelectorAll(".workspace-file-item"));
    const srcItem = items.find((el) => el.textContent?.includes("src/main.py")) as HTMLElement | undefined;
    expect(srcItem).toBeTruthy();

    await act(async () => {
      srcItem!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:/Projects/Piper/data/workspace/src/main.py",
    });
  });

  it("displays nested Windows backslash paths as relative labels and dispatches with original path", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:\\Projects\\Piper\\data\\workspace",
        files: [
          { name: "main.py", path: "C:\\Projects\\Piper\\data\\workspace\\src\\main.py", size: 1024 },
        ],
      });
    });

    expect(harness.container.textContent).toContain("src/main.py");
    expect(harness.container.textContent).not.toContain("C:\\Projects");

    const item = harness.container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(item).toBeTruthy();

    await act(async () => {
      item!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("read_workspace_file", {
      path: "C:\\Projects\\Piper\\data\\workspace\\src\\main.py",
    });
  });

  it("opens a nested workspace image with workspace-relative URL", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:/Projects/Piper/data/workspace",
        files: [
          { name: "capture_display.jpg", path: "C:/Projects/Piper/data/workspace/screens/capture_display.jpg", size: 2048 },
        ],
      });
    });

    const item = harness.container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(item).toBeTruthy();

    await act(async () => {
      item!.click();
    });

    const visionImage = harness.container.querySelector(".vision-image") as HTMLImageElement | null;
    expect(visionImage).toBeTruthy();
    expect(visionImage!.src).toContain("/workspace/screens/capture_display.jpg");
  });

  it("opens a nested Windows backslash image with encoded URL", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const toggle = harness.container.querySelector(".rail-workspace-toggle") as HTMLDivElement | null;
    expect(toggle).toBeTruthy();

    await act(async () => {
      toggle!.click();
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("workspace.files", {
        path: "C:\\Projects\\Piper\\data\\workspace",
        files: [
          { name: "capture display.jpg", path: "C:\\Projects\\Piper\\data\\workspace\\screens\\capture display.jpg", size: 2048 },
        ],
      });
    });

    const item = harness.container.querySelector(".workspace-file-item") as HTMLElement | null;
    expect(item).toBeTruthy();

    await act(async () => {
      item!.click();
    });

    const visionImage = harness.container.querySelector(".vision-image") as HTMLImageElement | null;
    expect(visionImage).toBeTruthy();
    expect(visionImage!.src).toContain("/workspace/screens/capture%20display.jpg");
  });
});
