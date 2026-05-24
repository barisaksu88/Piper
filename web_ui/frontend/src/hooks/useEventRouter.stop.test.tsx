import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { useEventRouter } from "./useEventRouter";
import type { ChatMessage } from "../types";

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

describe("useEventRouter stop settling", () => {
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

  it("settles stop state locally and ignores late stream.end", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    expect(router).toBeTruthy();

    await act(async () => {
      router!.handleFrame(makeFrame("stream.start"));
      router!.handleFrame(makeFrame("stream.delta", { text: "Stopping" }));
      vi.advanceTimersByTime(20);
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Stopping",
      streaming: true,
    });
    expect(router!.isGenerating).toBe(true);

    await act(async () => {
      router!.settleStreaming();
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Stopping",
      streaming: false,
    });
    expect(router!.messages.some((m: ChatMessage) => !m.content.trim())).toBe(false);
    expect(router!.isGenerating).toBe(false);

    await act(async () => {
      router!.handleFrame(makeFrame("stream.end"));
      vi.advanceTimersByTime(20);
    });

    expect(router!.messages).toHaveLength(1);
    expect(router!.messages[0]).toMatchObject({
      role: "assistant",
      content: "Stopping",
      streaming: false,
    });
    expect(router!.messages.some((m: ChatMessage) => !m.content.trim())).toBe(false);
    expect(router!.isGenerating).toBe(false);
  });
});
