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

describe("App action dispatch", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("dispatches send_message, new_session, and restart_piper from visible controls", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    expect(FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const chatInput = harness.container.querySelector(".chat-input") as HTMLInputElement | null;
    const sendButton = harness.container.querySelector(".send-btn") as HTMLButtonElement | null;
    const newSessionButton = harness.container.querySelector('button[title="New Session"]') as HTMLButtonElement | null;
    const restartButton = harness.container.querySelector('button[title="Restart"]') as HTMLButtonElement | null;

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

    expect(sendAction).toHaveBeenCalledWith("send_message", { text: "Hello Piper" });

    await act(async () => {
      newSessionButton!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("new_session", {});

    await act(async () => {
      restartButton!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("restart_piper", {});
  });
});
