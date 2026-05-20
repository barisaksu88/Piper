import { useRef } from "react";

interface EmptyWorkspaceProps {
  onFileSelected?: (files: FileList) => void;
}

export default function EmptyWorkspace({ onFileSelected }: EmptyWorkspaceProps) {
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

  return (
    <div className="workspace-empty">
      <div className="workspace-empty-icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
      </div>
      <p className="workspace-empty-text">Open a file to begin</p>
      <p className="workspace-empty-hint">.py for code · .txt/.md for text · images for vision</p>
      <button className="action-btn" onClick={handleOpenFile} type="button">
        Open File
      </button>
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
