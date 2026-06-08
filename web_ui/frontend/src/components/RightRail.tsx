import { useState, memo } from "react";
import RailCard from "./RailCard";
import type { LiveScreenState, RailPanelId, RawEventFilter } from "../types";

const EVENT_SPEECH_MODES = ["off", "noisy", "all"];
const LIVE_SCREEN_MODES = ["display", "window", "pointer"];
const LIVE_SCREEN_INTERVALS = [2, 5, 10, 15];
const RAW_EVENT_FILTERS: RawEventFilter[] = ["all", "errors", "system", "streaming"];

interface RightRailProps {
  expandedPanels: Record<RailPanelId, boolean>;
  onTogglePanel: (panel: RailPanelId) => void;
  workspaceOpen: boolean;
  onToggleWorkspace: () => void;
  connState: string;
  sendAction: (action: string, payload?: Record<string, unknown>) => boolean;
  documentIngestActive: boolean;
  documentsView: string;
  documentsViewRef: React.RefObject<HTMLDivElement | null>;
  selectedDocumentPaths: string[];
  documentPathInput: string;
  onDocumentPathChange: (value: string) => void;
  onAddDocumentPaths: () => void;
  onIngestSelected: () => void;
  onClearDocumentSelection: () => void;
  onCancelIngest: () => void;
  activities: string[];
  logs: string[];
  rawEvents: Array<{
    kind: string;
    sourceKind: string;
    payload: Record<string, unknown>;
    receivedAt: number;
  }>;
  rawEventFilter: RawEventFilter;
  onRawEventFilterChange: (filter: RawEventFilter) => void;
  liveScreen: LiveScreenState;
}

function CaptureAnalyze({
  sendAction,
  connState,
  liveScreen,
}: {
  sendAction: (action: string, payload?: Record<string, unknown>) => boolean;
  connState: string;
  liveScreen: LiveScreenState;
}) {
  const [prompt, setPrompt] = useState("");
  const canAnalyze = connState === "connected" && (liveScreen.enabled || liveScreen.lastCaptureTs > 0);
  return (
    <div className="capture-analyze">
      <input
        className="input-text capture-analyze-input"
        type="text"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && canAnalyze) {
            e.preventDefault();
            sendAction("screen_analyze", { prompt: prompt.trim() || undefined });
            setPrompt("");
          }
        }}
        placeholder="Ask about current screen..."
        disabled={connState !== "connected"}
      />
      <button
        className="capture-analyze-button"
        onClick={() => {
          sendAction("screen_analyze", { prompt: prompt.trim() || undefined });
          setPrompt("");
        }}
        disabled={!canAnalyze}
      >
        Analyze Current Frame
      </button>
    </div>
  );
}

function RightRail({
  expandedPanels,
  onTogglePanel,
  workspaceOpen,
  onToggleWorkspace,
  connState,
  sendAction,
  documentIngestActive,
  documentsView,
  documentsViewRef,
  selectedDocumentPaths,
  documentPathInput,
  onDocumentPathChange,
  onAddDocumentPaths,
  onIngestSelected,
  onClearDocumentSelection,
  onCancelIngest,
  activities,
  logs,
  rawEvents,
  rawEventFilter,
  onRawEventFilterChange,
  liveScreen,
}: RightRailProps) {
  return (
    <aside className="right-rail">
      <div
        className={`rail-workspace-toggle ${workspaceOpen ? "active" : ""}`}
        onClick={onToggleWorkspace}
        role="button"
        tabIndex={0}
      >
        <span className="rail-ws-label">Workspace</span>
        <span className="rail-ws-hint">{workspaceOpen ? "Close" : "Open"}</span>
      </div>

      <RailCard
        title="Capture"
        collapsible
        expanded={expandedPanels.capture}
        onToggle={() => onTogglePanel("capture")}
        badge={
          <span className={`rail-badge ${liveScreen.enabled ? "active" : liveScreen.pending ? "active" : ""}`}>
            {liveScreen.enabled ? "Live" : liveScreen.pending ? "Pending" : "Idle"}
          </span>
        }
      >
        <div className="capture-actions">
          <button
            className={`capture-toggle ${liveScreen.enabled ? "stop" : "start"}`}
            onClick={() => sendAction("snapshot_toggle")}
            disabled={connState !== "connected" || liveScreen.pending}
          >
            {liveScreen.pending ? "Starting..." : liveScreen.enabled ? "Stop Capture" : "Start Capture"}
          </button>
          {liveScreen.lastError && (
            <div className="capture-error">{liveScreen.lastError}</div>
          )}
        </div>
        <CaptureAnalyze sendAction={sendAction} connState={connState} liveScreen={liveScreen} />
        <div className="settings-row">
          <label className="setting-label">
            Event Speech
            <select
              onChange={(e) => sendAction("event_speech_mode", { mode: e.target.value })}
              disabled={connState !== "connected"}
              defaultValue="off"
            >
              {EVENT_SPEECH_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <label className="setting-label">
            Live Screen
            <select
              onChange={(e) => sendAction("live_screen_mode", { mode: e.target.value })}
              disabled={connState !== "connected"}
              defaultValue="display"
            >
              {LIVE_SCREEN_MODES.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </label>
          <label className="setting-label">
            Interval
            <select
              onChange={(e) => sendAction("live_screen_interval", { interval_s: Number(e.target.value) })}
              disabled={connState !== "connected"}
              defaultValue={10}
            >
              {LIVE_SCREEN_INTERVALS.map((n) => (
                <option key={n} value={n}>{n}s</option>
              ))}
            </select>
          </label>
        </div>
        <div className="capture-status">
          <div className="capture-status-row">
            <span className={`live-screen-dot ${liveScreen.pending ? "pending" : liveScreen.enabled ? "pending" : "idle"}`} />
            <span className="capture-status-label">
              {liveScreen.pending ? "Starting..." : liveScreen.enabled ? "Live" : "Idle"}
            </span>
          </div>
          {liveScreen.lastCaptureTs ? (
            <>
              <div className="capture-status-meta">
                Last capture: {new Date(liveScreen.lastCaptureTs * 1000).toLocaleTimeString()}
              </div>
              {liveScreen.lastCapturePath && (
                <div className="capture-status-meta">{liveScreen.lastCapturePath.replace(/^.*[\\/]/, "")}</div>
              )}
            </>
          ) : (
            <div className="capture-status-meta empty">No capture yet</div>
          )}
        </div>
      </RailCard>

      <RailCard
        title="Documents"
        collapsible
        expanded={expandedPanels.documents}
        onToggle={() => onTogglePanel("documents")}
        badge={
          <span className={documentIngestActive ? "rail-badge active" : "rail-badge"}>
            {documentIngestActive ? "Ingesting..." : "Idle"}
          </span>
        }
      >
        <div className="doc-panel">
          <div className="doc-view" ref={documentsViewRef}>
            {documentsView && <pre className="doc-view-content">{documentsView}</pre>}
          </div>
          {selectedDocumentPaths.length > 0 && (
            <div className="doc-selected-list">
              {selectedDocumentPaths.map((p, i) => (
                <div key={`dp-${i}`} className="doc-selected-item">{p}</div>
              ))}
            </div>
          )}
          <div className="doc-controls">
            <div className="doc-control-row">
              <input
                className="input-text doc-path"
                type="text"
                value={documentPathInput}
                onChange={(e) => onDocumentPathChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onAddDocumentPaths();
                  }
                }}
                placeholder="Path(s) separated by ; or newline..."
                disabled={connState !== "connected"}
              />
              <button onClick={onAddDocumentPaths} disabled={connState !== "connected" || !documentPathInput.trim()}>
                Add
              </button>
            </div>
            <div className="doc-control-row">
              <button onClick={onIngestSelected} disabled={connState !== "connected" || documentIngestActive || selectedDocumentPaths.length === 0}>
                Ingest Selected
              </button>
              <button onClick={onClearDocumentSelection} disabled={connState !== "connected"}>
                Clear
              </button>
              <button onClick={onCancelIngest} disabled={connState !== "connected" || !documentIngestActive}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      </RailCard>

      <RailCard
        title="Activity & Logs"
        collapsible
        expanded={expandedPanels.activity}
        onToggle={() => onTogglePanel("activity")}
      >
        <div className="log-box">
          {activities.map((a, i) => (
            <div key={`a-${i}`} className="log-line activity">{a}</div>
          ))}
          {logs.map((l, i) => (
            <div key={`l-${i}`} className="log-line log">{l}</div>
          ))}
        </div>
      </RailCard>

      <RailCard
        title="Raw Events"
        collapsible
        expanded={expandedPanels.raw}
        onToggle={() => onTogglePanel("raw")}
      >
        <div className="raw-event-filter">
          <label>
            Filter
            <select
              value={rawEventFilter}
              onChange={(e) => onRawEventFilterChange(e.target.value as RawEventFilter)}
            >
              {RAW_EVENT_FILTERS.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          </label>
          <span className="raw-event-count">{rawEvents.length} events</span>
        </div>
        <div className="log-box raw">
          {rawEvents.map((e, i) => (
            <details key={`e-${i}`} className="raw-event">
              <summary>{e.kind} ({e.sourceKind})</summary>
              <pre>{JSON.stringify(e.payload, null, 2)}</pre>
            </details>
          ))}
        </div>
      </RailCard>
    </aside>
  );
}

export default memo(RightRail);
