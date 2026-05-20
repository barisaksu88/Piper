import { useRef, useEffect, type KeyboardEvent, type ChangeEvent } from "react";

interface CodeWorkspaceProps {
  codeContent: string;
  onCodeChange: (content: string) => void;
  codeOutput: string[];
  codeRunning: boolean;
  codeStatus: string;
  codePath: string;
  onCodePathChange: (path: string) => void;
  onCodeRun: () => void;
  onCodeStop: () => void;
  onCodeClear: () => void;
  connState: string;
  // Stdin
  stdinText: string;
  onStdinChange: (text: string) => void;
  onStdinSend: () => void;
}

export default function CodeWorkspace({
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
  stdinText,
  onStdinChange,
  onStdinSend,
}: CodeWorkspaceProps) {
  const outputRef = useRef<HTMLDivElement>(null);
  const isConnected = connState === "connected";

  // Auto-scroll output
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [codeOutput]);

  // Ctrl+Enter runs
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      onCodeRun();
    }
  };

  // Enter sends stdin
  const handleStdinKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onStdinSend();
    }
  };

  return (
    <div className="code-workspace">
      {/* Toolbar */}
      <div className="workspace-toolbar">
        <div className="code-toolbar-left">
          <span className="workspace-title">Code</span>
          {codeRunning && <span className="code-running-badge">Running</span>}
          {codeStatus && !codeRunning && (
            <span className="code-status-badge">{codeStatus}</span>
          )}
        </div>
        <div className="code-toolbar-right">
          <button
            className="action-btn"
            onClick={onCodeClear}
            disabled={!isConnected || codeOutput.length === 0}
            type="button"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Body: editor + output */}
      <div className="code-body">
        <div className="code-editor-pane">
          <textarea
            className="code-editor"
            value={codeContent}
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onCodeChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="# Type Python code here&#10;# Ctrl+Enter to run"
            spellCheck={false}
          />
        </div>
        <div className="code-output-pane">
          <div className="code-output-header">Output</div>
          <div className="code-output-scroll" ref={outputRef}>
            {codeOutput.length === 0 ? (
              <div className="code-output-empty">No output yet</div>
            ) : (
              codeOutput.map((line, i) => (
                <div
                  key={i}
                  className={`code-output-line ${line.startsWith("Error") || line.startsWith("Traceback") ? "error" : ""}`}
                >
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Controls */}
      <div className="code-controls">
        <input
          className="input-text"
          type="text"
          value={codePath}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onCodePathChange(e.target.value)}
          placeholder="Script path (e.g. scripts/hello.py)"
        />
        <button
          className="action-btn primary"
          onClick={codeRunning ? onCodeStop : onCodeRun}
          disabled={!isConnected || (!codeRunning && !codePath.trim())}
          type="button"
        >
          {codeRunning ? "Stop" : "Run"}
        </button>
      </div>

      {/* Stdin */}
      <div className="code-stdin-row">
        <input
          className="input-text"
          type="text"
          value={stdinText}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onStdinChange(e.target.value)}
          onKeyDown={handleStdinKeyDown}
          placeholder="Stdin input..."
          disabled={!codeRunning}
        />
        <button
          className="action-btn"
          onClick={onStdinSend}
          disabled={!codeRunning || !stdinText.trim()}
          type="button"
        >
          Send
        </button>
      </div>
    </div>
  );
}
