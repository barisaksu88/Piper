import { useCallback } from "react";

interface SystemDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  connState: string;
  ttsState: string;
  logs: string[];
  userName?: string;
  backendVersion?: string;
}

export default function SystemDrawer({
  isOpen,
  onClose,
  connState,
  ttsState,
  logs,
  userName,
  backendVersion = "Piper",
}: SystemDrawerProps) {
  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose]
  );

  if (!isOpen) return null;

  const recentLogs = logs.slice(-50);

  return (
    <div className="system-drawer-backdrop" onClick={handleBackdropClick}>
      <div className="system-drawer">
        <div className="system-drawer-header">
          <h3>System</h3>
          <button className="icon-btn" onClick={onClose} title="Close">
            ✕
          </button>
        </div>

        <div className="system-drawer-body">
          {/* Connection */}
          <div className="system-section">
            <h4 className="system-section-title">Connection</h4>
            <div className="system-row">
              <span className="system-row-label">State</span>
              <span className="system-row-value">{connState}</span>
            </div>
            <div className="system-row">
              <span className="system-row-label">Backend</span>
              <span className="system-row-value">{backendVersion}</span>
            </div>
          </div>

          {/* User */}
          {userName && (
            <div className="system-section">
              <h4 className="system-section-title">Identity</h4>
              <div className="system-row">
                <span className="system-row-label">Active User</span>
                <span className="system-row-value">{userName}</span>
              </div>
            </div>
          )}

          {/* TTS */}
          <div className="system-section">
            <h4 className="system-section-title">TTS</h4>
            <div className="system-row">
              <span className="system-row-label">State</span>
              <span className="system-row-value">{ttsState}</span>
            </div>
          </div>

          {/* Logs */}
          <div className="system-section">
            <h4 className="system-section-title">Recent Events</h4>
            {recentLogs.length === 0 ? (
              <div className="system-empty">No events yet</div>
            ) : (
              <div className="system-stats-list">
                {recentLogs.map((line, i) => (
                  <div key={i} className="system-stat-line">
                    {line}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
