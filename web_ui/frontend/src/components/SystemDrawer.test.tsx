import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import SystemDrawer from "./SystemDrawer";

describe("SystemDrawer error section", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    root.unmount();
    container.remove();
  });

  it("renders backend errors separately from recent events", () => {
    act(() => {
      root.render(
        <SystemDrawer
          isOpen
          onClose={() => {}}
          connState="connected"
          ttsState="idle"
          errors={[
            {
              id: "err-1",
              message: "Backend failed",
              sourceKind: "",
              kind: "error",
              receivedAt: Date.now(),
            },
          ]}
          logs={["[Boot] Ready"]}
          userName="User"
          backendVersion="Piper v2.0"
        />
      );
    });

    expect(container.textContent).toContain("Errors");
    expect(container.textContent).toContain("Backend failed");
    expect(container.textContent).toContain("Recent Events");
    expect(container.textContent).toContain("[Boot] Ready");
  });
});
