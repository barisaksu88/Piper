import { useRef } from "react";

export interface WorkspaceFile {
  name: string;
  path: string;
  size: number;
}

interface EmptyWorkspaceProps {
  onFileSelected?: (files: FileList) => void;
  onFileFromList?: (path: string) => void;
  workspaceFiles?: WorkspaceFile[];
  workspacePath?: string;
}

export default function EmptyWorkspace({
  onFileSelected,
  onFileFromList,
  workspaceFiles,
  workspacePath,
}: EmptyWorkspaceProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleOpenFile = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0 && onFileSelected) {
      onFileSelected(files);
    }
    e.target.value = "";
  };

  const hasFiles = workspaceFiles && workspaceFiles.length > 0;

  return (
    <div className="workspace-empty">
      {hasFiles ? (
        <div className="workspace-file-list">
          <p className="workspace-file-list-path">
            {workspacePath?.replace(/\\/g, "/").split("/").slice(-3).join("/") || "workspace"}
          </p>
          <div className="workspace-file-list-scroll">
            {workspaceFiles.map((f) => (
              <div
                key={f.path}
                className="workspace-file-item"
                onClick={() => onFileFromList?.(f.path)}
                role="button"
                tabIndex={0}
              >
                <span className="workspace-file-name">{f.name}</span>
                <span className="workspace-file-size">{(f.size / 1024).toFixed(1)} KB</span>
              </div>
            ))}
          </div>
          <button className="action-btn" onClick={handleOpenFile} type="button" style={{ marginTop: "12px" }}>
            Open File from Computer
          </button>
        </div>
      ) : (
        <>
          <div className="workspace-empty-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/>
              <line x1="16" y1="17" x2="8" y2="17"/>
              <polyline points="10 9 9 9 8 9"/>
            </svg>
          </div>
          <p className="workspace-empty-text">No files yet</p>
          <p className="workspace-empty-hint">Open a file to begin</p>
          <button className="action-btn" onClick={handleOpenFile} type="button">
            Open File
          </button>
        </>
      )}
      <input
        ref={fileInputRef}
        type="file"
        style={{ display: "none" }}
        onChange={handleFileChange}
        accept=".py,.txt,.md,.jpg,.jpeg,.png,.webp"
      />
    </div>
  );
}
