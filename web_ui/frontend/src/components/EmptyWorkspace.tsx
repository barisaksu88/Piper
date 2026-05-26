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

  const getFileIcon = (name: string): string => {
    const ext = name.split('.').pop()?.toLowerCase() || '';
    if (ext === 'py') return '🐍';
    if (ext === 'txt' || ext === 'md') return '📄';
    if (['jpg', 'jpeg', 'png', 'webp'].includes(ext)) return '🖼️';
    return '📎';
  };

  const getWorkspaceRelativeLabel = (filePath: string, fileName: string): string => {
    const normalizedPath = filePath.replace(/\\/g, "/");
    const normalizedWorkspace = workspacePath?.replace(/\\/g, "/").replace(/\/+$/, "");

    if (normalizedWorkspace && normalizedPath.startsWith(`${normalizedWorkspace}/`)) {
      return normalizedPath.slice(normalizedWorkspace.length + 1);
    }

    return fileName;
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
          <p className="workspace-file-list-hint">Click a file to open</p>
          <div className="workspace-file-list-scroll">
            {workspaceFiles.map((f) => (
              <div
                key={f.path}
                className={`workspace-file-item workspace-file-${f.name.split('.').pop()?.toLowerCase() || 'file'}`}
                onClick={() => onFileFromList?.(f.path)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onFileFromList?.(f.path);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <span className="workspace-file-icon">{getFileIcon(f.name)}</span>
                <span className="workspace-file-name">{getWorkspaceRelativeLabel(f.path, f.name)}</span>
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
