import Workspace from "./Workspace";
import type { WorkspaceMode } from "../hooks/useWorkspace";

interface WorkspaceOverlayProps {
  workspace: {
    mode: WorkspaceMode;
    filePath: string;
    textContent: string;
    imageUrl: string;
    visionText: string;
    workspaceFiles: Array<{ name: string; path: string; size: number }>;
    workspacePath: string;
    setCodeContent: (content: string) => void;
    setTextContent: (content: string) => void;
    setVisionImage: (url: string) => void;
    openFile: (path: string, mode: "code" | "text" | "vision") => void;
    closeFile: () => void;
  };
  codePreview: string;
  onCodeChange: (content: string) => void;
  codeOutput: string[];
  codeRunning: boolean;
  codeStatus: string;
  codePath: string;
  onCodePathChange: (path: string) => void;
  onCodeRun: (content: string, path: string) => void;
  onCodeStop: () => void;
  onCodeClear: () => void;
  connState: string;
  stdinText: string;
  onStdinChange: (text: string) => void;
  onStdinSend: () => void;
  onTextContentChange: (content: string) => void;
  onTextSave: (content: string, fileName: string) => void;
  imageUrl: string;
  visionText: string;
  workspaceFiles: Array<{ name: string; path: string; size: number }>;
  workspacePath: string;
  onFileFromList: (path: string) => void;
  onFileSelected: (files: FileList | null) => void;
  onCloseWorkspace: () => void;
  onCloseFile: () => void;
  sendAction: (action: string, payload?: Record<string, unknown>) => boolean;
  setCodePathInput: (path: string) => void;
}

export default function WorkspaceOverlay({
  workspace,
  codePreview,
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
  onTextContentChange,
  onTextSave,
  imageUrl,
  visionText,
  workspaceFiles,
  workspacePath,
  onFileFromList,
  onCloseWorkspace,
}: WorkspaceOverlayProps) {
  return (
    <div className="workspace-overlay-full">
      <div className="workspace-overlay-header">
        <span className="workspace-overlay-title">Workspace</span>
        <button
          className="workspace-overlay-close"
          onClick={onCloseWorkspace}
          title="Close workspace"
          type="button"
        >
          ✕
        </button>
      </div>
      <div className="workspace-overlay-body">
        <Workspace
          mode={workspace.mode}
          filePath={workspace.filePath}
          onFileSelected={(files) => {
            const file = files?.[0];
            if (!file) return;
            const name = file.name.toLowerCase();
            if (name.endsWith(".py")) {
              workspace.openFile(file.name, "code");
              const reader = new FileReader();
              reader.onload = (e) => {
                const content = String(e.target?.result || "");
                onCodeChange(content);
                workspace.setCodeContent(content);
              };
              reader.readAsText(file);
            } else if (name.endsWith(".txt") || name.endsWith(".md")) {
              workspace.openFile(file.name, "text");
              const reader = new FileReader();
              reader.onload = (e) => {
                const content = String(e.target?.result || "");
                workspace.setTextContent(content);
              };
              reader.readAsText(file);
            } else if (/\.(jpg|jpeg|png|webp)$/.test(name)) {
              workspace.openFile(file.name, "vision");
              workspace.setVisionImage(URL.createObjectURL(file));
            }
          }}
          onClose={() => workspace.closeFile()}
          codeContent={codePreview}
          onCodeChange={onCodeChange}
          codeOutput={codeOutput}
          codeRunning={codeRunning}
          codeStatus={codeStatus}
          codePath={codePath}
          onCodePathChange={onCodePathChange}
          onCodeRun={onCodeRun}
          onCodeStop={onCodeStop}
          onCodeClear={onCodeClear}
          connState={connState}
          stdinText={stdinText}
          onStdinChange={onStdinChange}
          onStdinSend={onStdinSend}
          textContent={workspace.textContent}
          onTextContentChange={onTextContentChange}
          onTextSave={onTextSave}
          imageUrl={imageUrl}
          visionText={visionText}
          workspaceFiles={workspaceFiles}
          workspacePath={workspacePath}
          onFileFromList={onFileFromList}
        />
      </div>
    </div>
  );
}
