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

describe("useEventRouter mic status", () => {
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

  it("tracks backend mic.status transitions without mutating messages", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    expect(router).toBeTruthy();
    expect(router!.micStatus).toEqual({ state: "idle" });
    expect(router!.messages).toEqual([]);

    await act(async () => {
      router!.handleFrame(makeFrame("mic.status", {
        state: "listening",
        message: "Listening...",
      }));
    });
    expect(router!.micStatus).toEqual({
      state: "listening",
      message: "Listening...",
    });
    expect(router!.messages).toEqual([]);

    await act(async () => {
      router!.handleFrame(makeFrame("mic.status", {
        state: "transcribing",
        stage: "decoding",
        message: "Decoding audio...",
      }));
    });
    expect(router!.micStatus).toEqual({
      state: "transcribing",
      stage: "decoding",
      message: "Decoding audio...",
    });
    expect(router!.messages).toEqual([]);

    await act(async () => {
      router!.handleFrame(makeFrame("mic.status", {
        state: "error",
        error: "Piper is busy",
      }));
    });
    expect(router!.micStatus).toEqual({
      state: "error",
      error: "Piper is busy",
    });
    expect(router!.messages).toEqual([]);

    await act(async () => {
      router!.handleFrame(makeFrame("mic.status", {}));
    });
    expect(router!.micStatus).toEqual({ state: "idle" });
    expect(router!.messages).toEqual([]);
  });

  it("does not disturb streaming state when mic.status arrives", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { router = value; }} />);
    });

    await act(async () => {
      router!.handleFrame(makeFrame("stream.start"));
      router!.handleFrame(makeFrame("stream.delta", { text: "Hello" }));
      router!.handleFrame(makeFrame("mic.status", { state: "listening", message: "Listening..." }));
    });

    expect(router!.messages.some((m: ChatMessage) => m.content.includes("Hello"))).toBe(false);
    expect(router!.micStatus.state).toBe("listening");
  });
});
