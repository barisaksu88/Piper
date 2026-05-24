import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import VoiceStrip from "./VoiceStrip";

function renderVoiceStrip(props: Partial<Parameters<typeof VoiceStrip>[0]> = {}) {
  return (
    <VoiceStrip
      micState="idle"
      micButtonLabel="MIC"
      micButtonClass=""
      micDisabled={false}
      micStatusText=""
      backendMicStatus={{ state: "idle" }}
      onMicClick={() => {}}
      connState="connected"
      {...props}
    />
  );
}

describe("VoiceStrip mic status rendering", () => {
  let container: HTMLDivElement;
  let root: Root;

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
  });

  it("renders backend mic status and prefers it over local mic text", async () => {
    await act(async () => {
      root.render(
        renderVoiceStrip({
          micStatusText: "Local mic text",
          backendMicStatus: { state: "listening", message: "Listening..." },
        })
      );
    });

    expect(container.textContent).toContain("Listening...");
    expect(container.textContent).not.toContain("Local mic text");
  });

  it("falls back to local mic text when backend is idle", async () => {
    await act(async () => {
      root.render(
        renderVoiceStrip({
          micStatusText: "Local mic text",
          backendMicStatus: { state: "idle" },
        })
      );
    });

    expect(container.textContent).toContain("Local mic text");
  });

  it("renders backend transcribing message, stage fallback, and error", async () => {
    await act(async () => {
      root.render(
        renderVoiceStrip({
          backendMicStatus: {
            state: "transcribing",
            message: "Decoding audio...",
            stage: "decoding",
          },
        })
      );
    });
    expect(container.textContent).toContain("Decoding audio...");

    await act(async () => {
      root.render(
        renderVoiceStrip({
          backendMicStatus: {
            state: "transcribing",
            stage: "encoding",
          },
        })
      );
    });
    expect(container.textContent).toContain("encoding");

    await act(async () => {
      root.render(
        renderVoiceStrip({
          backendMicStatus: {
            state: "error",
            error: "Piper is busy",
          },
        })
      );
    });
    expect(container.textContent).toContain("Piper is busy");
  });
});
