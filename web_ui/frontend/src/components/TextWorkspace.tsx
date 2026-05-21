interface TextWorkspaceProps {
  fileName: string;
  content: string;
}

export default function TextWorkspace({ fileName, content }: TextWorkspaceProps) {
  return (
    <div className="text-workspace">
      <div className="workspace-toolbar">
        <span className="workspace-title">Text</span>
        <span className="text-meta">{fileName} · {content.length} characters</span>
      </div>
      <div className="text-body">
        <pre className="text-content">{content || "No content"}</pre>
      </div>
    </div>
  );
}
