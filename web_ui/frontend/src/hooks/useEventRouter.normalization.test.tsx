import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { useEventRouter } from "./useEventRouter";

function makeEventFrame(kind: string, payload: Record<string, unknown> = {}) {
  return {
    frame: "event" as const,
    timestamp: "2026-01-01T00:00:00.000Z",
    requestId: `req-${kind}`,
    kind,
    sourceKind: "smoke",
    payload,
  };
}

function makeErrorFrame(message: string) {
  return {
    frame: "error" as const,
    timestamp: "2026-01-01T00:00:00.000Z",
    requestId: "req-error",
    kind: "error",
    message,
    payload: {},
  };
}

function TestHost({ onReady }: { onReady: (value: ReturnType<typeof useEventRouter>) => void }) {
  const router = useEventRouter({
    setStatusText: vi.fn(),
    setModeText: vi.fn(),
    setUserName: vi.fn(),
    setStyleLabel: vi.fn(),
    setAuthWaiting: vi.fn(),
    setTtsState: vi.fn(),
    workspace: {
      openFile: vi.fn(),
      closeFile: vi.fn(),
      setCodeRunning: vi.fn(),
      clearCodeOutput: vi.fn(),
      appendCodeOutput: vi.fn(),
      setCodeContent: vi.fn(),
      setTextContent: vi.fn(),
      setWorkspaceFiles: vi.fn(),
      setWorkspacePath: vi.fn(),
      setVisionImage: vi.fn(),
    },
    setWorkspaceOpen: vi.fn(),
    isOperational: true,
  });

  useEffect(() => {
    onReady(router);
  }, [router, onReady]);

  return null;
}

describe("useEventRouter event normalization", () => {
  let container: HTMLDivElement;
  let root: Root;
  let router: ReturnType<typeof useEventRouter> | null = null;
  let rafSpy: any = null;
  let cancelSpy: any = null;

  beforeEach(() => {
    rafSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      cb(performance.now());
      return 1;
    });
    cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    router = null;
    rafSpy?.mockRestore();
    cancelSpy?.mockRestore();
    rafSpy = null;
    cancelSpy = null;
  });

  it("accepts canonical event frames and stream deltas", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeEventFrame("boot.ready"));
      router!.handleFrame(makeEventFrame("stream.start"));
      router!.handleFrame(makeEventFrame("stream.delta", { text: "Hello" }));
    });

    expect(router!.rawEvents).toHaveLength(3);
    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Hello",
    });
    expect(router!.isGenerating).toBe(true);
  });

  it("tracks bridge and event errors without mutating chat state unexpectedly", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeEventFrame("stream.start"));
      router!.handleFrame(makeEventFrame("stream.delta", { text: "Partial" }));
    });

    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Partial",
      streaming: true,
    });

    await act(async () => {
      router!.handleFrame(makeErrorFrame("Bridge failed"));
      router!.handleFrame(makeEventFrame("error", { message: "Event failed" }));
    });

    expect(router!.errors.map((err) => err.message)).toEqual(["Bridge failed", "Event failed"]);
    expect(router!.errors[0]).toMatchObject({ kind: "error", sourceKind: "" });
    expect(router!.errors[1]).toMatchObject({ kind: "error", sourceKind: "smoke" });
    expect(router!.rawEvents).toHaveLength(4);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Partial",
      streaming: false,
    });
    expect(router!.isGenerating).toBe(false);
    expect(router!.activities.some((line) => line.includes("Bridge failed"))).toBe(true);
    expect(router!.activities.some((line) => line.includes("Event failed"))).toBe(true);
  });

  it("ignores unknown event kinds without mutating chat or generating state", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeEventFrame("mystery.signal", { payload: "noop" }));
    });

    expect(router!.rawEvents).toHaveLength(1);
    expect(router!.messages).toEqual([]);
    expect(router!.isGenerating).toBe(false);
  });
});
