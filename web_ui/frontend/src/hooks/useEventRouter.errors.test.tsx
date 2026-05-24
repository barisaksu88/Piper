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

describe("useEventRouter error tracking", () => {
  let container: HTMLDivElement;
  let root: Root;
  let router: ReturnType<typeof useEventRouter> | null = null;

  beforeEach(() => {
    vi.useFakeTimers();
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
    vi.useRealTimers();
  });

  it("tracks top-level and event error frames without dropping raw events", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeEventFrame("stream.start"));
      router!.handleFrame(makeEventFrame("stream.delta", { text: "Partial" }));
      vi.advanceTimersByTime(20);
      router!.handleFrame(makeErrorFrame("Backend failed"));
      router!.handleFrame(makeEventFrame("error", { message: "Event failed" }));
    });

    expect(router!.errors).toHaveLength(2);
    expect(router!.errors[0]).toMatchObject({
      message: "Backend failed",
      kind: "error",
      sourceKind: "",
    });
    expect(router!.errors[1]).toMatchObject({
      message: "Event failed",
      kind: "error",
      sourceKind: "smoke",
    });
    expect(router!.rawEvents).toHaveLength(4);
    expect(router!.activities.some((line) => line.includes("Backend failed"))).toBe(true);
    expect(router!.activities.some((line) => line.includes("Event failed"))).toBe(true);
    expect(router!.messages[0]).toMatchObject({
      content: "Partial",
      streaming: false,
    });
  });

  it("appends distinct errors instead of replacing them", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeErrorFrame("First"));
      router!.handleFrame(makeErrorFrame("Second"));
    });

    expect(router!.errors.map((err) => err.message)).toEqual(["First", "Second"]);
  });
});
