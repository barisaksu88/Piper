import type { WorkspaceMode } from "../hooks/useWorkspace";
import EmptyWorkspace from "./EmptyWorkspace";
import CodeWorkspace from "./CodeWorkspace";
import TextWorkspace from "./TextWorkspace";
import VisionWorkspace from "./VisionWorkspace";
import type { WorkspaceFile } from "./EmptyWorkspace";

interface WorkspaceProps {
  mode: WorkspaceMode;
  filePath: string;
  onFileSelected?: (files: FileList) => void;
  onClose?: () => void;
  // Code mode props
  codeContent?: string;
  onCodeChange?: (content: string) => void;
  codeOutput?: string[];
  codeRunning?: boolean;
  codeStatus?: string;
  codePath?: string;
  onCodePathChange?: (path: string) => void;
  onCodeRun?: (content: string, path: string) => void;
  onCodeStop?: () => void;
  onCodeClear?: () => void;
  connState?: string;
  // Stdin
  stdinText?: string;
  onStdinChange?: (text: string) => void;
  onStdinSend?: () => void;
  // Text mode props
  textContent?: string;
  // Vision mode props
  imageUrl?: string;
  visionText?: string;
  // File list
  workspaceFiles?: WorkspaceFile[];
  workspacePath?: string;
  onFileFromList?: (path: string) => void;
}

export default function Workspace({
  mode,
  filePath,
  onFileSelected,
  onClose,
  codeContent,
  onCodeChange,
  codeOutput,
  codeRunning,
  codeStatus,
  codePath,
  onCodePathChange,
  onCodeRun,
  onCodeStop,
  onCodeClear,
  connState,
  stdinText,
  onStdinChange,
  onStdinSend,
  textContent,
  imageUrl,
  visionText,
  workspaceFiles,
  workspacePath,
  onFileFromList,
}: WorkspaceProps) {
  const displayName = filePath ? filePath.split("/").pop() || filePath : mode;

  return (
    <div className="workspace" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="workspace-toolbar">
        <span className="workspace-title">
          {mode === "empty" ? "" : displayName}
        </span>
        <div className="workspace-toolbar-actions">
          {mode !== "empty" && (
            <button
              className="workspace-close-btn"
              onClick={onClose}
              title="Close workspace file"
              type="button"
            >
              ✕
            </button>
          )}
        </div>
      </div>
      <div className="workspace-body" style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {mode === "empty" && (
          <EmptyWorkspace
            onFileSelected={onFileSelected}
            onFileFromList={onFileFromList}
            workspaceFiles={workspaceFiles}
            workspacePath={workspacePath}
          />
        )}
        {mode === "code" && (
          <CodeWorkspace
            codeContent={codeContent ?? ""}
            onCodeChange={onCodeChange ?? (() => {})}
            codeOutput={codeOutput ?? []}
            codeRunning={codeRunning ?? false}
            codeStatus={codeStatus ?? ""}
            codePath={codePath ?? ""}
            onCodePathChange={onCodePathChange ?? (() => {})}
            onCodeRun={onCodeRun ?? ((_) => {})}
            onCodeStop={onCodeStop ?? (() => {})}
            onCodeClear={onCodeClear ?? (() => {})}
            connState={connState ?? "disconnected"}
            stdinText={stdinText ?? ""}
            onStdinChange={onStdinChange ?? (() => {})}
            onStdinSend={onStdinSend ?? (() => {})}
          />
        )}
        {mode === "text" && (
          <TextWorkspace
            fileName={displayName}
            content={textContent ?? ""}
          />
        )}
        {mode === "vision" && (
          <VisionWorkspace
            fileName={displayName}
            imageUrl={imageUrl ?? ""}
            analysis={visionText}
          />
        )}
        {mode === "project" && <div className="workspace-placeholder">Project workspace — coming in next commit</div>}
      </div>
    </div>
  );
}
