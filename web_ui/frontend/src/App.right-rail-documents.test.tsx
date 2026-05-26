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

describe("App right rail documents action dispatch", () => {
  let harness: AppHarness;

  beforeEach(() => {
    harness = renderApp();
    sendAction.mockClear();
    FakeBridge.lastInstance = null;
  });

  afterEach(() => {
    cleanupApp(harness);
  });

  it("adds document paths, ingests, cancels, and clears selection", async () => {
    await act(async () => {
      harness.root.render(<App />);
    });

    expect(FakeBridge.lastInstance).toBeTruthy();

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("boot.ready");
    });

    const headers = Array.from(harness.container.querySelectorAll(".rail-card-header"));
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

    expect(sendAction).toHaveBeenCalledWith("document_picker_selected", {
      paths: ["C:/Docs/a.pdf", "C:/Docs/b.txt"],
    });

    await act(async () => {
      FakeBridge.lastInstance!.emitFrame("document.ingest_active", { active: true });
    });

    const cancelBtn = Array.from(documentsCard.querySelectorAll("button")).find(
      (b) => b.textContent === "Cancel"
    ) as HTMLButtonElement | undefined;
    expect(cancelBtn).toBeTruthy();
    expect(cancelBtn!.disabled).toBe(false);

    await act(async () => {
      cancelBtn!.click();
    });

    expect(sendAction).toHaveBeenCalledWith("document_picker_cancel", {});

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
