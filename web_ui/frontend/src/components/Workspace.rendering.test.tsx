import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

  it("renders text workspace and passes the full filePath to onTextSave", async () => {
    const onTextSave = vi.fn();
    await act(async () => {
      root.render(
        renderWorkspace({
          mode: "text",
          filePath: "C:/Projects/Piper/data/workspace/notes.txt",
          textContent: "hello world",
          onTextSave,
        })
      );
    });

    expect(container.textContent).toContain("notes.txt");

    const saveBtn = container.querySelector(".action-btn.primary") as HTMLButtonElement | null;
    expect(saveBtn).toBeTruthy();
    expect(saveBtn!.disabled).toBe(false);

    await act(async () => {
      saveBtn!.click();
    });

    expect(onTextSave).toHaveBeenCalledWith(
      "hello world",
      "C:/Projects/Piper/data/workspace/notes.txt"
    );
  });

  it("shows only the filename for Windows backslash paths and preserves the full path on save", async () => {
    const onTextSave = vi.fn();
    await act(async () => {
      root.render(
        renderWorkspace({
          mode: "text",
          filePath: "C:\\Projects\\Piper\\data\\workspace\\notes.txt",
          textContent: "backslash path",
          onTextSave,
        })
      );
    });

    expect(container.textContent).toContain("notes.txt");
    expect(container.textContent).not.toContain("C:\\Projects");

    const saveBtn = container.querySelector(".action-btn.primary") as HTMLButtonElement | null;
    expect(saveBtn).toBeTruthy();

    await act(async () => {
      saveBtn!.click();
    });

    expect(onTextSave).toHaveBeenCalledWith(
      "backslash path",
      "C:\\Projects\\Piper\\data\\workspace\\notes.txt"
    );
  });
});
