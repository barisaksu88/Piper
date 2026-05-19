import type { WorkspaceMode } from "../hooks/useWorkspace";
import EmptyWorkspace from "./EmptyWorkspace";

interface WorkspaceProps {
  mode: WorkspaceMode;
  onOpenFile: () => void;
}

export default function Workspace({ mode, onOpenFile }: WorkspaceProps) {
  return (
    <div className="workspace" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="workspace-toolbar">
        <span className="workspace-title">
          {mode === "empty" ? "Workspace" : mode.charAt(0).toUpperCase() + mode.slice(1)}
        </span>
      </div>
      <div className="workspace-body" style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {mode === "empty" && <EmptyWorkspace onOpenFile={onOpenFile} />}
        {mode === "code" && <div className="workspace-placeholder">Code workspace — coming in next commit</div>}
        {mode === "text" && <div className="workspace-placeholder">Text workspace — coming in next commit</div>}
        {mode === "vision" && <div className="workspace-placeholder">Vision workspace — coming in next commit</div>}
        {mode === "project" && <div className="workspace-placeholder">Project workspace — coming in next commit</div>}
      </div>
    </div>
  );
}
