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

describe("App right rail documents action dispatch", () => {
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

  it("adds document paths, ingests, cancels, and clears selection", async () => {
    await act(async () => {
      root.render(<App />);
    });

    expect(bridgeMock.FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const headers = Array.from(container.querySelectorAll(".rail-card-header"));
    const documentsHeader = headers.find((h) => h.textContent?.includes("Documents")) as HTMLElement | undefined;
    expect(documentsHeader).toBeTruthy();

    await act(async () => {
      documentsHeader!.click();
    });

    const documentsCard = documentsHeader!.closest(".rail-card") as HTMLElement;
    expect(documentsCard).toBeTruthy();

    const docInput = documentsCard.querySelector(".input-text.doc-path") as HTMLInputElement | null;
    expect(docInput).toBeTruthy();
    expect(docInput!.disabled).toBe(false);

    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setValue?.call(docInput, "C:/Docs/a.pdf; C:/Docs/b.txt");
      docInput!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const addBtn = Array.from(documentsCard.querySelectorAll("button")).find(
      (b) => b.textContent === "Add"
    ) as HTMLButtonElement | undefined;
    expect(addBtn).toBeTruthy();
    expect(addBtn!.disabled).toBe(false);

    await act(async () => {
      addBtn!.click();
    });

    const selectedItems = Array.from(documentsCard.querySelectorAll(".doc-selected-item"));
    expect(selectedItems.length).toBe(2);
    expect(selectedItems[0].textContent).toBe("C:/Docs/a.pdf");
    expect(selectedItems[1].textContent).toBe("C:/Docs/b.txt");

    const ingestBtn = Array.from(documentsCard.querySelectorAll("button")).find(
      (b) => b.textContent === "Ingest Selected"
    ) as HTMLButtonElement | undefined;
    expect(ingestBtn).toBeTruthy();
    expect(ingestBtn!.disabled).toBe(false);

    await act(async () => {
      ingestBtn!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("document_picker_selected", {
      paths: ["C:/Docs/a.pdf", "C:/Docs/b.txt"],
    });

    await act(async () => {
      bridgeMock.FakeBridge.lastInstance!.emitFrame("document.ingest_active", { active: true });
    });

    const cancelBtn = Array.from(documentsCard.querySelectorAll("button")).find(
      (b) => b.textContent === "Cancel"
    ) as HTMLButtonElement | undefined;
    expect(cancelBtn).toBeTruthy();
    expect(cancelBtn!.disabled).toBe(false);

    await act(async () => {
      cancelBtn!.click();
    });

    expect(bridgeMock.sendAction).toHaveBeenCalledWith("document_picker_cancel", {});

    const clearBtn = Array.from(documentsCard.querySelectorAll("button")).find(
      (b) => b.textContent === "Clear"
    ) as HTMLButtonElement | undefined;
    expect(clearBtn).toBeTruthy();
    expect(clearBtn!.disabled).toBe(false);

    await act(async () => {
      clearBtn!.click();
    });

    expect(documentsCard.querySelectorAll(".doc-selected-item").length).toBe(0);
  });
});
