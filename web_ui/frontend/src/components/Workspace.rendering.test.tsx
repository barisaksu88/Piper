import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import Workspace from "./Workspace";

function renderWorkspace(
  props: Partial<Parameters<typeof Workspace>[0]> & Pick<Parameters<typeof Workspace>[0], "mode" | "filePath">
) {
  const { mode, filePath, ...rest } = props;
  return (
    <Workspace
      mode={mode}
      filePath={filePath}
      onFileSelected={() => {}}
      onClose={() => {}}
      codeContent=""
      onCodeChange={() => {}}
      codeOutput={[]}
      codeRunning={false}
      codeStatus=""
      codePath=""
      onCodePathChange={() => {}}
      onCodeRun={() => {}}
      onCodeStop={() => {}}
      onCodeClear={() => {}}
      connState="connected"
      stdinText=""
      onStdinChange={() => {}}
      onStdinSend={() => {}}
      textContent=""
      onTextContentChange={() => {}}
      onTextSave={() => {}}
      imageUrl=""
      visionText=""
      workspaceFiles={[]}
      workspacePath=""
      onFileFromList={() => {}}
      {...rest}
    />
  );
}

describe("Workspace rendering", () => {
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

  it("renders code workspace details, running state, and output", async () => {
    await act(async () => {
      root.render(
        renderWorkspace({
          mode: "code",
          filePath: "scripts/example.py",
          codeContent: "print('hello')",
          codeOutput: ["hello", "exited with code 0"],
          codeRunning: true,
          codeStatus: "",
          codePath: "scripts/example.py",
        })
      );
    });

    expect(container.textContent).toContain("example.py");
    expect(container.textContent).toContain("print('hello')");
    expect(container.textContent).toContain("hello");
    expect(container.textContent).toContain("Running");
  });

  it("renders the empty workspace fallback", async () => {
    await act(async () => {
      root.render(
        renderWorkspace({
          mode: "empty",
          filePath: "",
        })
      );
    });

    expect(container.textContent).toContain("No files yet");
    expect(container.textContent).toContain("Open a file to begin");
  });
});
