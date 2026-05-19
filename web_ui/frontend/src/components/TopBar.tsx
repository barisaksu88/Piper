import type { ConnectionState } from "../types";

interface TopBarProps {
  connState: ConnectionState;
  statusText: string;
  modeText: string;
  onNewSession: () => void;
  onRestart: () => void;
  onStop: () => void;
  onOpenSystem: () => void;
  workspaceOpen: boolean;
  onToggleWorkspace: () => void;
}

export default function TopBar({
  connState,
  statusText,
  modeText,
  onNewSession,
  onRestart,
  onStop,
  onOpenSystem,
  workspaceOpen,
  onToggleWorkspace,
}: TopBarProps) {
  const connBadge =
    connState === "connected"
      ? "badge connected"
      : connState === "connecting"
      ? "badge connecting"
      : connState === "error"
      ? "badge error"
      : "badge disconnected";

  return (
    <header className="top-bar">
      <div className="top-bar-brand">
        <div className="brand-icon">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 2L2 7l10 5 10-5-10-5z" />
            <path d="M2 17l10 5 10-5" />
            <path d="M2 12l10 5 10-5" />
          </svg>
        </div>
        <div className="brand-text">
          <div className="brand-title">PIPER</div>
          <div className="brand-subtitle">
            <span
              className={`status-dot ${
                connState === "connected" ? "online" : "offline"
              }`}
            />
            Local Mode · {statusText || "Ready"}
          </div>
        </div>
      </div>

      <nav className="top-bar-nav">
        <button className="nav-tab active">Chat</button>
      </nav>

      <div className="top-bar-actions">
        <span className={connBadge}>{connState}</span>
        {modeText && <span className="mode-pill">{modeText}</span>}
        <button
          className="icon-btn"
          onClick={onStop}
          title="Stop"
          disabled={connState !== "connected"}
        >
          ■
        </button>
        <button
          className="icon-btn"
          onClick={onNewSession}
          title="New Session"
          disabled={connState !== "connected"}
        >
          +
        </button>
        <button
          className="icon-btn danger"
          onClick={onRestart}
          title="Restart"
          disabled={connState !== "connected"}
        >
          ↻
        </button>
        <button
          className={`icon-btn ${workspaceOpen ? "active" : ""}`}
          onClick={onToggleWorkspace}
          title="Workspace"
          type="button"
        >
          W
        </button>
        <button
          className="icon-btn"
          onClick={onOpenSystem}
          title="System"
          type="button"
        >
          ⚙
        </button>
      </div>
    </header>
  );
}
