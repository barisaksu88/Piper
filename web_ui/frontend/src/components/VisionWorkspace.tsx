import { useRef } from "react";

interface VisionWorkspaceProps {
  fileName: string;
  imageUrl: string;
  analysis?: string;
}

export default function VisionWorkspace({ fileName, imageUrl, analysis }: VisionWorkspaceProps) {
  const imgRef = useRef<HTMLImageElement>(null);

  const handleImageClick = () => {
    const img = imgRef.current;
    if (!img) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      img.requestFullscreen().catch(() => {
        // Fallback: open in new tab if fullscreen fails
        window.open(imageUrl, "_blank");
      });
    }
  };

  return (
    <div className="vision-workspace">
      <div className="workspace-toolbar">
        <span className="workspace-title">Vision</span>
        <span className="text-meta">{fileName}</span>
      </div>
      <div className="vision-body">
        <div className="vision-image-area">
          <img
            ref={imgRef}
            src={imageUrl}
            alt={fileName || "Image"}
            className="vision-image"
            onClick={handleImageClick}
            title="Click to view fullscreen"
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
