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

describe("useEventRouter top-level error frame", () => {
  let container: HTMLDivElement;
  let root: Root;
  let router: ReturnType<typeof useEventRouter> | null = null;
  let originalRaf: typeof window.requestAnimationFrame;
  let originalCancel: typeof window.cancelAnimationFrame;

  beforeEach(() => {
    originalRaf = window.requestAnimationFrame;
    originalCancel = window.cancelAnimationFrame;
    // @ts-expect-error intentional override to force setTimeout fallback
    delete window.requestAnimationFrame;
    // @ts-expect-error intentional override to force setTimeout fallback
    delete window.cancelAnimationFrame;
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
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCancel;
  });

  it("settles an in-flight stream and preserves partial text on error", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeEventFrame("stream.start"));
      router!.handleFrame(makeEventFrame("stream.delta", { text: "Partial" }));
      vi.advanceTimersByTime(20);
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Partial",
      streaming: true,
    });
    expect(router!.isGenerating).toBe(true);

    await act(async () => {
      router!.handleFrame(makeErrorFrame("Backend failed"));
    });

    expect(router!.isGenerating).toBe(false);
    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Partial",
      streaming: false,
    });
    expect(router!.activities[router!.activities.length - 1]).toContain("Backend failed");
    expect(router!.rawEvents).toHaveLength(3);
    expect(router!.rawEvents[2]).toMatchObject({
      kind: "error",
      sourceKind: "",
      payload: {},
    });
  });

  it("removes an empty assistant bubble when error arrives before any delta", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeEventFrame("stream.start"));
      router!.handleFrame(makeErrorFrame("Backend failed"));
    });

    expect(router!.isGenerating).toBe(false);
    expect(router!.messages).toHaveLength(0);
    expect(router!.activities[router!.activities.length - 1]).toContain("Backend failed");
    expect(router!.rawEvents).toHaveLength(2);
  });
});
