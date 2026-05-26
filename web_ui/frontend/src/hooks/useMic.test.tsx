import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { useMic } from "./useMic";
import type { PiperBridge } from "../bridge";

const utilsMock = vi.hoisted(() => ({
  blobToBase64: vi.fn(),
  chooseMimeType: vi.fn(),
  formatFromMimeType: vi.fn(),
}));

vi.mock("../utils", () => ({
  blobToBase64: utilsMock.blobToBase64,
  chooseMimeType: utilsMock.chooseMimeType,
  formatFromMimeType: utilsMock.formatFromMimeType,
}));

interface MockMediaRecorder {
  state: string;
  mimeType: string;
  ondataavailable: ((ev: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: (() => void) | null;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
}

let lastMockRecorder: MockMediaRecorder | null = null;

function createMockMediaRecorder(): MockMediaRecorder {
  const recorder: MockMediaRecorder = {
    state: "inactive",
    mimeType: "audio/webm",
    ondataavailable: null,
    onstop: null,
    onerror: null,
    start: vi.fn(() => {
      recorder.state = "recording";
    }),
    stop: vi.fn(() => {
      recorder.state = "inactive";
      if (recorder.onstop) {
        recorder.onstop();
      }
    }),
  };
  return recorder;
}

function setupMediaMocks(options: { getUserMediaSuccess?: boolean } = {}) {
  const trackStop = vi.fn();
  const mockStream = {
    getTracks: vi.fn(() => [{ stop: trackStop }]),
  };

  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    value: {
      getUserMedia: vi.fn(async () => {
        if (options.getUserMediaSuccess === false) {
          throw new Error("Permission denied");
        }
        return mockStream;
      }),
    },
    writable: true,
    configurable: true,
  });

  Object.defineProperty(globalThis, "MediaRecorder", {
    value: vi.fn(() => {
      const recorder = createMockMediaRecorder();
      lastMockRecorder = recorder;
      return recorder;
    }),
    writable: true,
    configurable: true,
  });

  Object.defineProperty(globalThis.MediaRecorder, "isTypeSupported", {
    value: vi.fn(() => true),
    writable: true,
    configurable: true,
  });

  return { mockStream, trackStop };
}

function TestHost({
  bridgeRef,
  onReady,
}: {
  bridgeRef: React.RefObject<PiperBridge | null>;
  onReady: (value: ReturnType<typeof useMic>) => void;
}) {
  const mic = useMic({ bridgeRef, appendActivity: vi.fn() });

  useEffect(() => {
    onReady(mic);
  }, [mic, onReady]);

  return null;
}

describe("useMic browser recording", () => {
  let container: HTMLDivElement;
  let root: Root;
  let mic: ReturnType<typeof useMic> | null = null;
  let bridgeSendAction: ReturnType<typeof vi.fn>;
  let bridgeRef: React.RefObject<PiperBridge | null>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    bridgeSendAction = vi.fn(() => true);
    bridgeRef = {
      current: {
        sendAction: bridgeSendAction,
      } as unknown as PiperBridge,
    };
    lastMockRecorder = null;
    utilsMock.blobToBase64.mockReset();
    utilsMock.chooseMimeType.mockReset();
    utilsMock.formatFromMimeType.mockReset();
    utilsMock.chooseMimeType.mockReturnValue("audio/webm");
    utilsMock.formatFromMimeType.mockReturnValue("webm");
    utilsMock.blobToBase64.mockResolvedValue("fakebase64");
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    mic = null;
  });

  async function renderHost() {
    await act(async () => {
      root.render(
        <TestHost
          bridgeRef={bridgeRef}
          onReady={(value) => {
            mic = value;
          }}
        />
      );
    });
  }

  it("successful recording submits audio", async () => {
    setupMediaMocks();
    await renderHost();

    expect(mic!.micState).toBe("idle");

    await act(async () => {
      mic!.startMicRecording();
    });

    expect(mic!.micState).toBe("listening");

    const blob = new Blob(["audio"], { type: "audio/webm" });
    act(() => {
      lastMockRecorder!.ondataavailable!({ data: blob });
    });

    await act(async () => {
      mic!.stopMicRecording();
    });

    expect(bridgeSendAction).toHaveBeenCalledWith("mic_audio_submit", {
      audio: "fakebase64",
      format: "webm",
      sample_rate_hint: 48000,
    });
  });

  it("send failure enters error", async () => {
    setupMediaMocks();
    bridgeSendAction.mockReturnValue(false);
    await renderHost();

    await act(async () => {
      mic!.startMicRecording();
    });

    const blob = new Blob(["audio"], { type: "audio/webm" });
    act(() => {
      lastMockRecorder!.ondataavailable!({ data: blob });
    });

    await act(async () => {
      mic!.stopMicRecording();
    });

    expect(mic!.micState).toBe("error");
    expect(mic!.micError).toBe("Failed to send audio to backend");
  });

  it("permission failure enters error", async () => {
    setupMediaMocks({ getUserMediaSuccess: false });
    await renderHost();

    await act(async () => {
      try {
        await mic!.startMicRecording();
      } catch {
        // hook catches internally
      }
    });

    expect(mic!.micState).toBe("error");
    expect(mic!.micError).toBe("Microphone permission denied or unavailable");
  });

  it("abort while listening discards and returns to idle", async () => {
    setupMediaMocks();
    await renderHost();

    await act(async () => {
      mic!.startMicRecording();
    });

    expect(mic!.micState).toBe("listening");

    await act(async () => {
      mic!.abortMicRecording();
    });

    expect(mic!.micState).toBe("idle");
    expect(mic!.micError).toBe("");
    expect(bridgeSendAction).not.toHaveBeenCalledWith(
      "mic_audio_submit",
      expect.any(Object)
    );
  });

  it("backend idle acknowledgement clears transcribing state", async () => {
    setupMediaMocks();
    await renderHost();

    await act(async () => {
      mic!.startMicRecording();
    });

    const blob = new Blob(["audio"], { type: "audio/webm" });
    act(() => {
      lastMockRecorder!.ondataavailable!({ data: blob });
    });

    await act(async () => {
      mic!.stopMicRecording();
    });

    expect(mic!.micState).toBe("transcribing");

    act(() => {
      mic!.handleBackendMicStatus({ state: "idle" });
    });

    expect(mic!.micState).toBe("idle");
    expect(mic!.micError).toBe("");
    expect(mic!.micStageMessage).toBe("");
  });

  it("backend error acknowledgement clears transcribing to error", async () => {
    setupMediaMocks();
    await renderHost();

    await act(async () => {
      mic!.startMicRecording();
    });

    const blob = new Blob(["audio"], { type: "audio/webm" });
    act(() => {
      lastMockRecorder!.ondataavailable!({ data: blob });
    });

    await act(async () => {
      mic!.stopMicRecording();
    });

    expect(mic!.micState).toBe("transcribing");

    act(() => {
      mic!.handleBackendMicStatus({ state: "error", error: "Backend mic failed" });
    });

    expect(mic!.micState).toBe("error");
    expect(mic!.micError).toBe("Backend mic failed");
  });
});
