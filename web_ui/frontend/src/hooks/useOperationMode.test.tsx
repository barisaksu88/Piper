import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useEffect } from "react";
import { useOperationMode } from "./useOperationMode";

function TestHost({
  onReady,
}: {
  onReady: (value: ReturnType<typeof useOperationMode>) => void;
}) {
  const mode = useOperationMode();

  useEffect(() => {
    onReady(mode);
  }, [mode, onReady]);

  return null;
}

describe("useOperationMode", () => {
  let container: HTMLDivElement;
  let root: Root;
  let hook: ReturnType<typeof useOperationMode> | null = null;

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
    hook = null;
  });

  it("starts booting and becomes operational on boot.ready", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { hook = value; }} />);
    });

    expect(hook).toBeTruthy();
    expect(hook!.mode).toBe("booting");
    expect(hook!.isOperational).toBe(false);
    expect(hook!.bootMessage).toBe("Initializing Piper...");

    await act(async () => {
      hook!.handleBootReady();
    });

    expect(hook!.mode).toBe("chat");
    expect(hook!.isOperational).toBe(true);
    expect(hook!.bootMessage).toBe("");
  });

  it("marks boot logs as progress and errors", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { hook = value; }} />);
    });

    await act(async () => {
      hook!.handleBootLog("Loaded X");
    });
    expect(hook!.steps).toEqual([{ name: "Loaded X", status: "done" }]);
    expect(hook!.bootMessage).toBe("Initializing Piper...");

    await act(async () => {
      hook!.handleBootLog("Error loading X");
    });
    expect(hook!.steps).toEqual([
      { name: "Loaded X", status: "done" },
      { name: "Error loading X", status: "error" },
    ]);
    expect(hook!.bootMessage).toBe("Error: Error loading X");
  });
});
