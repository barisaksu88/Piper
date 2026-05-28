import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import LiveScreenPanel from "./LiveScreenPanel";

describe("LiveScreenPanel", () => {
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

  it("shows pending state with timestamp", () => {
    act(() => {
      root.render(<LiveScreenPanel liveScreen={{ pending: true, lastRefreshAt: Date.now() }} />);
    });
    expect(container.textContent).toContain("Capture pending");
    expect(container.textContent).toContain("Last refresh:");
  });

  it("shows idle state when not pending and no refresh", () => {
    act(() => {
      root.render(<LiveScreenPanel liveScreen={{ pending: false, lastRefreshAt: null }} />);
    });
    expect(container.textContent).toContain("Idle");
    expect(container.textContent).toContain("No refresh yet");
  });
});
