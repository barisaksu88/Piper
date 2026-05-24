import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { useEventRouter } from "./useEventRouter";

function makeFrame(kind: string, payload: Record<string, unknown> = {}) {
  return {
    frame: "event" as const,
    timestamp: "2026-01-01T00:00:00.000Z",
    requestId: `req-${kind}`,
    kind,
    sourceKind: "smoke",
    payload,
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

describe("useEventRouter stream delta backpressure", () => {
  let container: HTMLDivElement;
  let root: Root;
  let router: ReturnType<typeof useEventRouter> | null = null;

  beforeEach(() => {
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
  });

  it("batches a delta burst into one frame flush and settles on end", async () => {
    const originalRaf = window.requestAnimationFrame;
    const originalCancel = window.cancelAnimationFrame;
    const rafCallbacks = new Map<number, FrameRequestCallback>();
    const rafSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      const id = rafCallbacks.size + 1;
      rafCallbacks.set(id, cb);
      return id;
    });
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      rafCallbacks.delete(id);
    });

    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("stream.start"));
      router!.handleFrame(makeFrame("stream.delta", { text: "A" }));
      router!.handleFrame(makeFrame("stream.delta", { text: "B" }));
      router!.handleFrame(makeFrame("stream.delta", { text: "C" }));
    });

    expect(rafSpy).toHaveBeenCalledTimes(1);
    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({ content: "", streaming: true });
    expect(router!.isGenerating).toBe(true);

    const frame = rafCallbacks.values().next().value as FrameRequestCallback;
    await act(async () => {
      frame(performance.now());
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "ABC",
      streaming: true,
    });
    expect(router!.isGenerating).toBe(true);

    await act(async () => {
      router!.handleFrame(makeFrame("stream.end"));
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "ABC",
      streaming: false,
    });
    expect(router!.isGenerating).toBe(false);

    rafSpy.mockRestore();
    cancelSpy.mockRestore();
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCancel;
  });

  it("falls back to timeout batching when rAF is unavailable", async () => {
    const originalRaf = window.requestAnimationFrame;
    const originalCancel = window.cancelAnimationFrame;
    // @ts-expect-error intentional environment override for fallback coverage
    delete window.requestAnimationFrame;
    // @ts-expect-error intentional environment override for fallback coverage
    delete window.cancelAnimationFrame;
    vi.useFakeTimers();

    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("stream.start"));
      router!.handleFrame(makeFrame("stream.delta", { text: "Hello" }));
      router!.handleFrame(makeFrame("stream.delta", { text: " " }));
      router!.handleFrame(makeFrame("stream.delta", { text: "world" }));
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({ content: "", streaming: true });

    await act(async () => {
      vi.advanceTimersByTime(16);
    });

    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Hello world",
      streaming: true,
    });

    await act(async () => {
      router!.handleFrame(makeFrame("stream.end"));
    });

    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Hello world",
      streaming: false,
    });
    expect(router!.isGenerating).toBe(false);

    vi.useRealTimers();
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCancel;
  });
});
