interface VisionWorkspaceProps {
  fileName: string;
  imageUrl: string;
  analysis?: string;
}

export default function VisionWorkspace({ fileName, imageUrl, analysis }: VisionWorkspaceProps) {
  return (
    <div className="vision-workspace">
      <div className="workspace-toolbar">
        <span className="workspace-title">{fileName || "Vision"}</span>
      </div>
      <div className="vision-body">
        <div className="vision-image-area">
          <img
            src={imageUrl}
            alt={fileName || "Image"}
            className="vision-image"
            onClick={() => window.open(imageUrl, "_blank")}
          />
        </div>
        {analysis && (
          <div className="vision-analysis">
            <h4>Analysis</h4>
            <p>{analysis}</p>
          </div>
        )}
      </div>
    </div>
  );
}
