import { useCallback, useEffect, useRef, useState } from "react";
import { PiperBridge } from "./bridge";
import type { BackendFrame, ChatMessage, ConnectionState, RawEvent } from "./types";

const EVENT_SPEECH_MODES = ["off", "noisy", "all"];
const LIVE_SCREEN_MODES = ["display", "window", "pointer"];
const LIVE_SCREEN_INTERVALS = [2, 5, 10, 15];
const DELTA_COALESCE_MS = 16;

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function isThinkingPlaceholder(m: ChatMessage): boolean {
  return m.role === "system" && (m.content === "Thinking..." || m.content.startsWith("Thinking"));
}

function messageSignature(m: { role: string; content: string }): string {
  return `${m.role}:${m.content}`;
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
  const chatBoxRef = useRef<HTMLDivElement | null>(null);
  const pendingDeltasRef = useRef("");
  const deltaFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-scroll chat to bottom when messages change
  useEffect(() => {
    const el = chatBoxRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const flushPendingDeltas = useCallback(() => {
    const text = pendingDeltasRef.current;
    pendingDeltasRef.current = "";
    if (deltaFlushTimerRef.current) {
      clearTimeout(deltaFlushTimerRef.current);
      deltaFlushTimerRef.current = null;
    }
    if (!text) return;

    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        next[next.length - 1] = { ...last, content: last.content + text };
      } else {
        next.push({ id: generateId(), role: "assistant", content: text, streaming: true });
        streamingRef.current = true;
      }
      return next;
    });
  }, []);

  const queueDelta = useCallback(
    (text: string) => {
      pendingDeltasRef.current += text;
      if (deltaFlushTimerRef.current) {
        clearTimeout(deltaFlushTimerRef.current);
      }
      deltaFlushTimerRef.current = setTimeout(() => {
        flushPendingDeltas();
      }, DELTA_COALESCE_MS);
    },
    [flushPendingDeltas]
  );

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

  const clearThinkingPlaceholders = useCallback(() => {
    setMessages((prev) => prev.filter((m) => !isThinkingPlaceholder(m)));
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
        case "chat.sync": {
          const p = payload as { messages?: Array<{ role?: string; content?: string }> };
          const syncMessages = (p.messages || []).map((m) => ({
            role: String(m.role || "system"),
            content: String(m.content || ""),
          }));
          const syncSigs = new Set(syncMessages.map(messageSignature));

          setMessages((prev) => {
            // Preserve:
            // 1. Local user messages not yet in sync (e.g. sent during reconnect)
            // 2. Currently streaming assistant message
            const preserved = prev.filter((m) => {
              if (m.streaming && m.role === "assistant") return true;
              if (m.role === "user" && m.content && !syncSigs.has(messageSignature(m))) return true;
              return false;
            });
            const built = [
              ...preserved,
              ...syncMessages.map((m) => ({
                id: generateId(),
                role: m.role as ChatMessage["role"],
                content: m.content,
                streaming: false,
              })),
            ];
            return built;
          });
          break;
        }

        case "stream.start": {
          flushPendingDeltas();
          streamingRef.current = true;
          clearThinkingPlaceholders();
          setMessages((prev) => [
            ...prev,
            { id: generateId(), role: "assistant", content: "", streaming: true },
          ]);
          break;
        }

        case "stream.delta": {
          const text = String((payload as { text?: string }).text || "");
          if (!text) break;
          if (!streamingRef.current) {
            streamingRef.current = true;
            setMessages((prev) => [
              ...prev,
              { id: generateId(), role: "assistant", content: text, streaming: true },
            ]);
          } else {
            queueDelta(text);
          }
          break;
        }

        case "stream.end": {
          flushPendingDeltas();
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
          clearThinkingPlaceholders();
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
          appendActivity(
            `[Error] ${String((payload as { message?: string }).message || "Unknown error")}`
          );
          break;
        }

        default:
          // Unhandled kinds go to raw inspector only
          break;
      }
    },
    [appendActivity, appendLog, addRawEvent, clearThinkingPlaceholders, flushPendingDeltas, queueDelta]
  );

  useEffect(() => {
    const bridge = new PiperBridge({
      onStateChange: setConnState,
      onFrame: handleFrame,
      onError: (msg) => appendActivity(`[Bridge Error] ${msg}`),
    });
    bridgeRef.current = bridge;
    bridge.connect();
    return () => {
      bridge.disconnect();
      if (deltaFlushTimerRef.current) {
        clearTimeout(deltaFlushTimerRef.current);
      }
    };
  }, [handleFrame, appendActivity]);

  const sendAction = useCallback((action: string, payload: Record<string, unknown> = {}) => {
    bridgeRef.current?.sendAction(action, payload);
  }, []);

  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;
    setInputText("");
    setMessages((prev) => [...prev, { id: generateId(), role: "user", content: text }]);
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
          <div className="chat-messages" ref={chatBoxRef}>
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
