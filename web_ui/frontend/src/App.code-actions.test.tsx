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

describe("App code active controls dispatch", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("dispatches code_send on stdin Send and stop on Stop when process is active", async () => {
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

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("file.contents", {
        path: "C:/Projects/Piper/data/workspace/main.py",
        name: "main.py",
        content: "print('hello')",
      });
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("code.active", { active: true });
    });

    const stopBtn = harness.container.querySelector(".code-controls .action-btn.primary") as HTMLButtonElement | null;
    expect(stopBtn).toBeTruthy();
    expect(stopBtn!.textContent).toBe("Stop");
    expect(stopBtn!.disabled).toBe(false);

    const stdinInput = harness.container.querySelector(".code-stdin-row .input-text") as HTMLInputElement | null;
    expect(stdinInput).toBeTruthy();
    expect(stdinInput!.disabled).toBe(false);

    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setValue?.call(stdinInput, "hello stdin");
      stdinInput!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const sendBtn = harness.container.querySelector(".code-stdin-row .action-btn") as HTMLButtonElement | null;
    expect(sendBtn).toBeTruthy();
    expect(sendBtn!.textContent).toBe("Send");
    expect(sendBtn!.disabled).toBe(false);

    await act(async () => {
      sendBtn!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("code_send", { text: "hello stdin" });

    await act(async () => {
      stopBtn!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("stop", {});
  });
});
