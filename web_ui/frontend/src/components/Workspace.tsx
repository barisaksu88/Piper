import type { WorkspaceMode } from "../hooks/useWorkspace";
import EmptyWorkspace from "./EmptyWorkspace";
import CodeWorkspace from "./CodeWorkspace";

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
  onCodeRun?: () => void;
  onCodeStop?: () => void;
  onCodeClear?: () => void;
  connState?: string;
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
        {mode === "empty" && <EmptyWorkspace onFileSelected={onFileSelected} />}
        {mode === "code" && (
          <CodeWorkspace
            codeContent={codeContent ?? ""}
            onCodeChange={onCodeChange ?? (() => {})}
            codeOutput={codeOutput ?? []}
            codeRunning={codeRunning ?? false}
            codeStatus={codeStatus ?? ""}
            codePath={codePath ?? ""}
            onCodePathChange={onCodePathChange ?? (() => {})}
            onCodeRun={onCodeRun ?? (() => {})}
            onCodeStop={onCodeStop ?? (() => {})}
            onCodeClear={onCodeClear ?? (() => {})}
            connState={connState ?? "disconnected"}
          />
        )}
        {mode === "text" && <div className="workspace-placeholder">Text workspace — coming in next commit</div>}
        {mode === "vision" && <div className="workspace-placeholder">Vision workspace — coming in next commit</div>}
        {mode === "project" && <div className="workspace-placeholder">Project workspace — coming in next commit</div>}
      </div>
    </div>
  );
}
