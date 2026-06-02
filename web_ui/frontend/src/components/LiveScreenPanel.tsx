import type { LiveScreenState } from "../types";

interface LiveScreenPanelProps {
  liveScreen: LiveScreenState;
}

export default function LiveScreenPanel({ liveScreen }: LiveScreenPanelProps) {
  return (
    <div className="live-screen-panel">
      <div className="live-screen-status">
        <span
          className={`live-screen-dot ${liveScreen.pending ? "pending" : "idle"}`}
          aria-label={liveScreen.pending ? "Pending" : "Idle"}
        />
        <span className="live-screen-label">
          {liveScreen.pending ? "Capture pending" : "Idle"}
        </span>
      </div>
      {liveScreen.lastRefreshAt ? (
        <div className="live-screen-meta">
          Last refresh: {new Date(liveScreen.lastRefreshAt).toLocaleTimeString()}
        </div>
      ) : (
        <div className="live-screen-meta empty">No refresh yet</div>
      )}
    </div>
  );
}
