import { type ChangeEvent } from "react";

interface TextWorkspaceProps {
  fileName: string;
  content: string;
  onContentChange: (content: string) => void;
  onSave: (content: string, fileName: string) => void;
  connState: string;
}

export default function TextWorkspace({
  fileName,
  content,
  onContentChange,
  onSave,
  connState,
}: TextWorkspaceProps) {
  const isConnected = connState === "connected";

  return (
    <div className="text-workspace">
      <div className="workspace-toolbar">
        <div className="code-toolbar-left">
          <span className="workspace-title">Text</span>
          <span className="text-meta">{fileName} · {content.length} characters</span>
        </div>
        <div className="code-toolbar-right">
          <button
            className="action-btn primary"
            onClick={() => onSave(content, fileName)}
            disabled={!isConnected}
            type="button"
          >
            Save
          </button>
        </div>
      </div>
      <div className="text-body">
        <textarea
          className="text-editor"
          value={content}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onContentChange(e.target.value)}
          placeholder="Type text here..."
          spellCheck={false}
        />
      </div>
    </div>
  );
}
