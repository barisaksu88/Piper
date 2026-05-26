import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EmptyWorkspace from "./EmptyWorkspace";

function renderEmptyWorkspace(
  props: Partial<Parameters<typeof EmptyWorkspace>[0]> = {}
) {
  return (
    <EmptyWorkspace
      onFileSelected={() => {}}
      onFileFromList={() => {}}
      workspaceFiles={[]}
      workspacePath=""
      {...props}
    />
  );
}

describe("EmptyWorkspace file list labels", () => {
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

  it("lists top-level files as basename", async () => {
    await act(async () => {
      root.render(
        renderEmptyWorkspace({
          workspacePath: "/home/user/workspace",
          workspaceFiles: [
            { name: "counter.py", path: "/home/user/workspace/counter.py", size: 1024 },
          ],
        })
      );
    });

    expect(container.textContent).toContain("counter.py");
  });

  it("lists nested files with workspace-relative labels", async () => {
    await act(async () => {
      root.render(
        renderEmptyWorkspace({
          workspacePath: "/home/user/workspace",
          workspaceFiles: [
            { name: "main.py", path: "/home/user/workspace/src/main.py", size: 512 },
            { name: "main.py", path: "/home/user/workspace/tests/main.py", size: 256 },
          ],
        })
      );
    });

    expect(container.textContent).toContain("src/main.py");
    expect(container.textContent).toContain("tests/main.py");
  });

  it("normalizes Windows backslash paths to forward-slash relative labels", async () => {
    await act(async () => {
      root.render(
        renderEmptyWorkspace({
          workspacePath: "C:\\Projects\\Piper\\data\\workspace",
          workspaceFiles: [
            { name: "main.py", path: "C:\\Projects\\Piper\\data\\workspace\\src\\main.py", size: 100 },
          ],
        })
      );
    });

    expect(container.textContent).toContain("src/main.py");
    expect(container.textContent).not.toContain("C:\\Projects");
  });

  it("calls onFileFromList with the original full path when a nested file is clicked", async () => {
    const onFileFromList = vi.fn();
    await act(async () => {
      root.render(
        renderEmptyWorkspace({
          workspacePath: "/home/user/workspace",
          workspaceFiles: [
            { name: "main.py", path: "/home/user/workspace/src/main.py", size: 100 },
          ],
          onFileFromList,
        })
      );
    });

    const fileItem = container.querySelector(".workspace-file-item") as HTMLDivElement | null;
    expect(fileItem).toBeTruthy();

    await act(async () => {
      fileItem!.click();
    });

    expect(onFileFromList).toHaveBeenCalledWith("/home/user/workspace/src/main.py");
  });
});
