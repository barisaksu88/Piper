import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useEffect } from "react";
import { useWorkspace } from "./useWorkspace";

function TestHost({ onReady }: { onReady: (value: ReturnType<typeof useWorkspace>) => void }) {
  const workspace = useWorkspace();

  useEffect(() => {
    onReady(workspace);
  }, [workspace, onReady]);

  return null;
}

describe("useWorkspace", () => {
  let container: HTMLDivElement;
  let root: Root;
  let workspace: ReturnType<typeof useWorkspace> | null = null;

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
    workspace = null;
  });

  it("tracks code, text, vision, and workspace file selection state", async () => {
    await act(async () => {
      root.render(<TestHost onReady={(value) => { workspace = value; }} />);
    });

    expect(workspace!.mode).toBe("empty");
    expect(workspace!.filePath).toBe("");
    expect(workspace!.codeContent).toBe("");
    expect(workspace!.codeOutput).toEqual([]);
    expect(workspace!.codeRunning).toBe(false);
    expect(workspace!.textContent).toBe("");
    expect(workspace!.imageUrl).toBe("");
    expect(workspace!.visionText).toBe("");

    await act(async () => {
      workspace!.setWorkspaceFiles([
        { name: "main.py", path: "/tmp/main.py", size: 1024 },
      ]);
      workspace!.setWorkspacePath("/tmp/project");
      workspace!.openFile("main.py", "code");
      workspace!.setCodeContent("print('hello')");
      workspace!.appendCodeOutput("hello");
      workspace!.setCodeRunning(true);
      workspace!.setStdinText("stdin");
    });

    expect(workspace!.workspaceFiles).toHaveLength(1);
    expect(workspace!.workspacePath).toBe("/tmp/project");
    expect(workspace!.mode).toBe("code");
    expect(workspace!.filePath).toBe("main.py");
    expect(workspace!.codeContent).toBe("print('hello')");
    expect(workspace!.codeOutput).toEqual(["hello"]);
    expect(workspace!.codeRunning).toBe(true);
    expect(workspace!.stdinText).toBe("stdin");

    await act(async () => {
      workspace!.openFile("notes.txt", "text");
      workspace!.setTextContent("hello text");
      workspace!.setVisionImage("blob://img");
      workspace!.setVisionText("image analysis");
    });

    expect(workspace!.mode).toBe("text");
    expect(workspace!.filePath).toBe("notes.txt");
    expect(workspace!.textContent).toBe("hello text");
    expect(workspace!.imageUrl).toBe("blob://img");
    expect(workspace!.visionText).toBe("image analysis");

    await act(async () => {
      workspace!.closeFile();
    });

    expect(workspace!.mode).toBe("empty");
    expect(workspace!.filePath).toBe("");
    expect(workspace!.codeContent).toBe("");
    expect(workspace!.codeOutput).toEqual([]);
    expect(workspace!.codeRunning).toBe(false);
    expect(workspace!.stdinText).toBe("");
    expect(workspace!.textContent).toBe("");
    expect(workspace!.imageUrl).toBe("");
    expect(workspace!.visionText).toBe("");
  });
});
