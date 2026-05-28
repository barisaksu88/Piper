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

describe("useEventRouter visibility events", () => {
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

  it("tracks live screen refresh state", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("screen.refresh", { pending: true }));
    });

    expect(router!.liveScreen.pending).toBe(true);
    expect(router!.liveScreen.lastRefreshAt).toBeTruthy();
    expect(router!.activities.some((a) => a.includes("Screen refresh: pending"))).toBe(true);

    await act(async () => {
      router!.handleFrame(makeFrame("screen.refresh", { pending: false }));
    });

    expect(router!.liveScreen.pending).toBe(false);
  });

  it("tracks stats refresh state", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    const payload = {
      summary_text: "3 turns",
      record_count: 3,
      turn_numbers: [1, 2, 3],
      total_ms: [100, 200, 300],
    };

    await act(async () => {
      router!.handleFrame(makeFrame("stats.refresh", payload));
    });

    expect(router!.stats.summaryText).toBe("3 turns");
    expect(router!.stats.recordCount).toBe(3);
    expect(router!.stats.turnNumbers).toEqual([1, 2, 3]);
    expect(router!.stats.totalMs).toEqual([100, 200, 300]);
    expect(router!.stats.receivedAt).toBeTruthy();
  });

  it("logs config reload with changed keys", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("config.reloaded", { changed_keys: ["MODEL", "VOICE"] }));
    });

    expect(router!.logs.some((l) => l.includes("MODEL") && l.includes("VOICE"))).toBe(true);
  });

  it("filters raw events by category", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("stream.start"));
      router!.handleFrame(makeFrame("stream.delta", { text: "hi" }));
      router!.handleFrame(makeFrame("error", { message: "fail" }));
      router!.handleFrame(makeFrame("boot.log", { text: "ready" }));
      router!.handleFrame(makeFrame("chat.append", { role: "user", content: "hello" }));
    });

    expect(router!.rawEvents).toHaveLength(5);

    await act(async () => {
      router!.setRawEventFilter("streaming");
    });
    expect(router!.filteredRawEvents).toHaveLength(2);
    expect(router!.filteredRawEvents.every((e) => e.kind.startsWith("stream."))).toBe(true);

    await act(async () => {
      router!.setRawEventFilter("errors");
    });
    expect(router!.filteredRawEvents).toHaveLength(1);
    expect(router!.filteredRawEvents[0].kind).toBe("error");

    await act(async () => {
      router!.setRawEventFilter("system");
    });
    expect(router!.filteredRawEvents).toHaveLength(1);
    expect(router!.filteredRawEvents[0].kind).toBe("boot.log");

    await act(async () => {
      router!.setRawEventFilter("all");
    });
    expect(router!.filteredRawEvents).toHaveLength(5);
  });

  it("resets visibility state on reset", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("screen.refresh", { pending: true }));
      router!.handleFrame(makeFrame("stats.refresh", { summary_text: "x" }));
      router!.setRawEventFilter("errors");
    });

    await act(async () => {
      router!.reset();
    });

    expect(router!.liveScreen.pending).toBe(false);
    expect(router!.stats.recordCount).toBe(0);
    expect(router!.rawEventFilter).toBe("all");
  });
});
