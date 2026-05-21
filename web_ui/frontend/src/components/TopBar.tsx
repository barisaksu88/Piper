import type { ConnectionState } from "../types";

interface TopBarProps {
  connState: ConnectionState;
  statusText: string;
  modeText: string;
  onNewSession: () => void;
  onRestart: () => void;
  onStop: () => void;
  onOpenSystem: () => void;
}

export default function TopBar({
  connState,
  statusText,
  modeText,
  onNewSession,
  onRestart,
  onStop,
  onOpenSystem,
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
        <img
          src="/piper-logo.png"
          alt="Piper"
          className="brand-logo-img"
          draggable={false}
        />
        <div className="brand-subtitle">
          <span
            className={`status-dot ${
              connState === "connected" ? "online" : "offline"
            }`}
          />
          {statusText || "Ready"}
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
