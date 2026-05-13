import { useCallback, useEffect, useRef, useState } from "react";
import { PiperBridge } from "./bridge";
import type { BackendFrame, ChatMessage, ConnectionState, RawEvent } from "./types";

const EVENT_SPEECH_MODES = ["off", "noisy", "all"];
const LIVE_SCREEN_MODES = ["display", "window", "pointer"];
const LIVE_SCREEN_INTERVALS = [2, 5, 10, 15];

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function App() {
  const [connState, setConnState] = useState<ConnectionState>("disconnected");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [statusText, setStatusText] = useState("IDLE");
  const [modeText, setModeText] = useState("");
  const [stepText, setStepText] = useState("");
  const [activities, setActivities] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [rawEvents, setRawEvents] = useState<RawEvent[]>([]);
  const [inputText, setInputText] = useState("");
  const streamingRef = useRef(false);
  const bridgeRef = useRef<PiperBridge | null>(null);

  const appendActivity = useCallback((text: string) => {
    setActivities((prev) => [...prev.slice(-199), text]);
  }, []);

  const appendLog = useCallback((text: string) => {
    setLogs((prev) => [...prev.slice(-199), text]);
  }, []);

  const addRawEvent = useCallback((frame: BackendFrame) => {
    setRawEvents((prev) => [
      ...prev.slice(-199),
      {
        kind: frame.kind,
        sourceKind: "sourceKind" in frame ? String(frame.sourceKind) : "",
        payload: frame.payload,
        receivedAt: Date.now(),
      },
    ]);
  }, []);

  const handleFrame = useCallback(
    (frame: BackendFrame) => {
      if (frame.frame === "error") {
        appendActivity(`[Error] ${frame.message}`);
        addRawEvent(frame);
        return;
      }

      const { kind, payload } = frame;
      addRawEvent(frame);

      // Suppression check
      if ((payload as Record<string, unknown>)._suppressed) {
        return;
      }

      switch (kind) {
        case "stream.start": {
          streamingRef.current = true;
          setMessages((prev) => [
            ...prev,
            { id: generateId(), role: "assistant", content: "", streaming: true },
          ]);
          break;
        }
        case "stream.delta": {
          const text = String((payload as { text?: string }).text || "");
          if (!streamingRef.current) {
            // stray delta without start
            setMessages((prev) => [
              ...prev,
              { id: generateId(), role: "assistant", content: text, streaming: true },
            ]);
            streamingRef.current = true;
          } else {
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant" && last.streaming) {
                next[next.length - 1] = { ...last, content: last.content + text };
              } else {
                next.push({ id: generateId(), role: "assistant", content: text, streaming: true });
              }
              return next;
            });
          }
          break;
        }
        case "stream.end": {
          streamingRef.current = false;
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              next[next.length - 1] = { ...last, streaming: false };
            }
            return next;
          });
          break;
        }
        case "chat.append": {
          const p = payload as { role?: string; content?: string };
          const role = String(p.role || "system") as ChatMessage["role"];
          const content = String(p.content || "");
          if (!content) break;
          setMessages((prev) => [
            ...prev,
            { id: generateId(), role, content, streaming: false },
          ]);
          break;
        }
        case "chat.clear_thinking": {
          setMessages((prev) =>
            prev.filter((m) => m.content !== "Thinking...")
          );
          break;
        }
        case "status.set": {
          setStatusText(String((payload as { text?: string }).text || "IDLE"));
          break;
        }
        case "status.mode": {
          setModeText(String((payload as { text?: string }).text || ""));
          break;
        }
        case "status.step": {
          setStepText(String((payload as { text?: string }).text || ""));
          break;
        }
        case "activity.append": {
          appendActivity(String((payload as { text?: string }).text || ""));
          break;
        }
        case "boot.log": {
          appendLog(`[Boot] ${String((payload as { text?: string }).text || "")}`);
          break;
        }
        case "boot.ready": {
          appendLog("[Boot] Ready");
          break;
        }
        case "log.agent": {
          appendLog(`[Agent] ${String((payload as { text?: string }).text || "")}`);
          break;
        }
        case "error": {
          appendActivity(`[Error] ${String((payload as { message?: string }).message || "Unknown error")}`);
          break;
        }
        default:
          // Unhandled kinds go to raw inspector only
          break;
      }
    },
    [appendActivity, appendLog, addRawEvent]
  );

  useEffect(() => {
    const bridge = new PiperBridge({
      onStateChange: setConnState,
      onFrame: handleFrame,
      onError: (msg) => appendActivity(`[Bridge Error] ${msg}`),
    });
    bridgeRef.current = bridge;
    bridge.connect();
    return () => bridge.disconnect();
  }, [handleFrame, appendActivity]);

  const sendAction = useCallback((action: string, payload: Record<string, unknown> = {}) => {
    bridgeRef.current?.sendAction(action, payload);
  }, []);

  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;
    setInputText("");
    setMessages((prev) => [
      ...prev,
      { id: generateId(), role: "user", content: text },
    ]);
    sendAction("send_message", { text });
  }, [inputText, sendAction]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const connBadge =
    connState === "connected"
      ? "badge connected"
      : connState === "connecting"
      ? "badge connecting"
      : connState === "error"
      ? "badge error"
      : "badge disconnected";

  return (
    <div className="app">
      <header className="header">
        <h1>Piper Web UI</h1>
        <span className={connBadge}>{connState}</span>
      </header>

      <main className="main">
        <section className="chat-panel">
          <h2>Chat</h2>
          <div className="chat-messages">
            {messages.map((m) => (
              <div
                key={m.id}
                className={`message ${m.role} ${m.streaming ? "streaming" : ""}`}
              >
                <span className="message-role">{m.role}</span>
                <pre className="message-content">{m.content}</pre>
              </div>
            ))}
          </div>
        </section>

        <aside className="sidebar">
          <div className="sidebar-section">
            <h3>Status</h3>
            <div className="status-box">
              <div className="status-line">{statusText}</div>
              {modeText && <div className="status-line mode">{modeText}</div>}
              {stepText && <div className="status-line step">{stepText}</div>}
            </div>
          </div>

          <div className="sidebar-section">
            <h3>Activity & Logs</h3>
            <div className="log-box">
              {activities.map((a, i) => (
                <div key={`a-${i}`} className="log-line activity">
                  {a}
                </div>
              ))}
              {logs.map((l, i) => (
                <div key={`l-${i}`} className="log-line log">
                  {l}
                </div>
              ))}
            </div>
          </div>

          <div className="sidebar-section inspector">
            <h3>Raw Events</h3>
            <div className="log-box raw">
              {rawEvents.map((e, i) => (
                <details key={`e-${i}`} className="raw-event">
                  <summary>
                    {e.kind} ({e.sourceKind})
                  </summary>
                  <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                </details>
              ))}
            </div>
          </div>
        </aside>
      </main>

      <footer className="controls">
        <div className="control-row">
          <input
            className="input-text"
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            disabled={connState !== "connected"}
          />
          <button onClick={handleSend} disabled={connState !== "connected"}>
            Send
          </button>
          <button onClick={() => sendAction("stop")} disabled={connState !== "connected"}>
            Stop
          </button>
          <button onClick={() => sendAction("new_session")} disabled={connState !== "connected"}>
            New Session
          </button>
          <button
            className="danger"
            onClick={() => sendAction("restart_piper")}
            disabled={connState !== "connected"}
          >
            Restart
          </button>
        </div>

        <div className="control-row">
          <label>
            Event Speech
            <select
              onChange={(e) => sendAction("event_speech_mode", { mode: e.target.value })}
              disabled={connState !== "connected"}
              defaultValue="off"
            >
              {EVENT_SPEECH_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          <label>
            Live Screen
            <select
              onChange={(e) => sendAction("live_screen_mode", { mode: e.target.value })}
              disabled={connState !== "connected"}
              defaultValue="display"
            >
              {LIVE_SCREEN_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>

          <label>
            Interval (s)
            <select
              onChange={(e) =>
                sendAction("live_screen_interval", { interval_s: Number(e.target.value) })
              }
              disabled={connState !== "connected"}
              defaultValue={10}
            >
              {LIVE_SCREEN_INTERVALS.map((n) => (
                <option key={n} value={n}>
                  {n}s
                </option>
              ))}
            </select>
          </label>

          <span className="placeholder">Mic: deferred</span>
          <span className="placeholder">Docs: deferred</span>
          <span className="placeholder">Image: placeholder</span>
        </div>
      </footer>
    </div>
  );
}
