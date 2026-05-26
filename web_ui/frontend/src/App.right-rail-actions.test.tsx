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

describe("App right rail capture action dispatch", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("dispatches event_speech_mode, live_screen_mode, and live_screen_interval from Capture controls", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    expect(FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const headers = Array.from(harness.container.querySelectorAll(".rail-card-header"));
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

    expect(sendAction).toHaveBeenCalledWith("event_speech_mode", { mode: "all" });

    await act(async () => {
      liveScreenSelect.value = "pointer";
      liveScreenSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(sendAction).toHaveBeenCalledWith("live_screen_mode", { mode: "pointer" });

    await act(async () => {
      intervalSelect.value = "5";
      intervalSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(sendAction).toHaveBeenCalledWith("live_screen_interval", { interval_s: 5 });
  });
});
