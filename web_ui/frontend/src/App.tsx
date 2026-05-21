import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { PiperBridge, WS_URL } from "./bridge";
import type { BackendFrame, ChatMessage, ConnectionState, RawEvent } from "./types";
import TopBar from "./components/TopBar";
import ChatPanel from "./components/ChatPanel";
import AvatarStage from "./components/AvatarStage";
import ModeSelector from "./components/ModeSelector";
import VoiceStrip from "./components/VoiceStrip";
import StatusFooter from "./components/StatusFooter";
import OperationScreen from "./components/OperationScreen";
import SystemDrawer from "./components/SystemDrawer";
import Workspace from "./components/Workspace";
import { useOperationMode } from "./hooks/useOperationMode";
import { usePiperUI } from "./hooks/usePiperUI";
import { useWorkspace } from "./hooks/useWorkspace";

const EVENT_SPEECH_MODES = ["off", "noisy", "all"];
const LIVE_SCREEN_MODES = ["display", "window", "pointer"];
const LIVE_SCREEN_INTERVALS = [2, 5, 10, 15];
const DELTA_COALESCE_MS = 16;
const MAX_CODE_OUTPUT_LINES = 500;

type MicState = "idle" | "requesting_permission" | "listening" | "transcribing" | "error";
type TtsState = "idle" | "synthesizing" | "playing" | "error";
type RailPanelId = "code" | "documents" | "system" | "activity" | "raw" | "capture";

interface RailCardProps {
  title: string;
  children: ReactNode;
  badge?: ReactNode;
  compact?: boolean;
  collapsible?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
}

function sanitizeOperationalText(text: string): string {
  const clean = String(text || "").trim();
  if (!clean) return "";
  if (clean.toUpperCase().includes("SPEAK")) return "Generating";
  return clean;
}

function RailCard({
  title,
  children,
  badge,
  compact = false,
  collapsible = false,
  expanded = true,
  onToggle,
}: RailCardProps) {
  const bodyId = `rail-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const className = [
    "rail-card",
    compact ? "compact" : "",
    collapsible ? "collapsible" : "",
    collapsible && expanded ? "expanded" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={className}>
      <button
        type="button"
        className="rail-card-header"
        onClick={collapsible ? onToggle : undefined}
        aria-expanded={collapsible ? expanded : undefined}
        aria-controls={collapsible ? bodyId : undefined}
        aria-disabled={!collapsible}
        tabIndex={collapsible ? 0 : -1}
      >
        <h3>{title}</h3>
        <span className="rail-card-header-meta">
          {badge}
          {collapsible && <span className="rail-toggle">{expanded ? "Collapse" : "Expand"}</span>}
        </span>
      </button>
      {(!collapsible || expanded) && (
        <div className="rail-card-body" id={bodyId}>
          {children}
        </div>
      )}
    </div>
  );
}

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function isThinkingPlaceholder(m: ChatMessage): boolean {
  const text = m.content.trim();
  return (
    (m.role === "assistant" || m.role === "system") &&
    (text === "Thinking..." || text === "Thinking…" || text.startsWith("Thinking"))
  );
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result as string;
      // Strip data URI prefix if present
      const commaIdx = result.indexOf(",");
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function chooseMimeType(): string {
  const prefs = [
    "audio/webm;codecs=opus",
    "audio/webm",
  ];
  for (const t of prefs) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return "";
}

function formatFromMimeType(mime: string): "webm" | "wav" {
  if (mime.includes("wav")) return "wav";
  return "webm";
}

export default function App() {
  const { steps, bootMessage, handleBootLog, handleBootReady, handleBootProgress, isOperational } = useOperationMode();

  const [connState, setConnState] = useState<ConnectionState>("disconnected");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const ui = usePiperUI();
  const {
    mode: modeText, setMode: setModeText,
    statusText, setStatusText,
    styleLabel, setStyleLabel,
    userName, setUserName,
    authWaiting, setAuthWaiting,
    ttsState, setTtsState,
    workspaceOpen, setWorkspaceOpen,
    resetUI,
  } = ui;

  const [isGenerating, setIsGenerating] = useState(false);
  const [activities, setActivities] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [rawEvents, setRawEvents] = useState<RawEvent[]>([]);
  const [inputText, setInputText] = useState("");

  // Code session state
  const [codeOutput, setCodeOutput] = useState<string[]>([]);
  const [codeStatus, setCodeStatus] = useState("idle");
  const [codeActive, setCodeActive] = useState(false);
  const [codePreview, setCodePreview] = useState("");
  const [codePathInput, setCodePathInput] = useState("");
  const [codeInputText, setCodeInputText] = useState("");

  // Document ingestion state
  const [documentsView, setDocumentsView] = useState("");
  const [documentIngestActive, setDocumentIngestActive] = useState(false);
  const [documentPathInput, setDocumentPathInput] = useState("");
  const [selectedDocumentPaths, setSelectedDocumentPaths] = useState<string[]>([]);
  // documentStatus placeholder removed — not currently used by backend events

  // Image / vision state


  // System / identity state

  const [systemDrawerOpen, setSystemDrawerOpen] = useState(false);
  const workspace = useWorkspace();

  const [expandedRailPanels, setExpandedRailPanels] = useState<Record<RailPanelId, boolean>>({
    code: false,
    documents: false,
    system: false,
    activity: false,
    raw: false,
    capture: false,
  });

  // Mic state
  const [micState, setMicState] = useState<MicState>("idle");
  const [micError, setMicError] = useState("");
  const [micStageMessage, setMicStageMessage] = useState("");
  const micStateRef = useRef<MicState>("idle");
  const discardNextMicStopRef = useRef(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const micWatchdogTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const micSubmitTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    micStateRef.current = micState;
  }, [micState]);

  // Mic watchdog: warn if transcribing hangs for too long
  useEffect(() => {
    if (micWatchdogTimerRef.current) {
      clearTimeout(micWatchdogTimerRef.current);
      micWatchdogTimerRef.current = null;
    }
    if (micState === "transcribing") {
      micWatchdogTimerRef.current = setTimeout(() => {
        setMicStageMessage("Transcription is taking too long. Check backend console.");
      }, 90000);
    }
    return () => {
      if (micWatchdogTimerRef.current) {
        clearTimeout(micWatchdogTimerRef.current);
        micWatchdogTimerRef.current = null;
      }
    };
  }, [micState]);

  const experimentalMicUpload = import.meta.env.VITE_PIPER_EXPERIMENTAL_MIC_UPLOAD === "true";

  const IMAGE_BASE_URL = WS_URL.replace(/^ws:\/\//, "http://").replace(/\/ws$/, "");

  const streamingRef = useRef(false);
  const bridgeRef = useRef<PiperBridge | null>(null);
  const chatBoxRef = useRef<HTMLDivElement | null>(null);
  const codeInputRef = useRef<HTMLInputElement | null>(null);
  const documentsViewRef = useRef<HTMLDivElement | null>(null);

  const pendingDeltasRef = useRef("");
  const deltaFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-scroll chat to bottom when messages change
  useEffect(() => {
    const el = chatBoxRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  // Auto-scroll documents view to bottom
  useEffect(() => {
    const el = documentsViewRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [documentsView]);

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

  const appendCodeOutput = useCallback((text: string) => {
    setCodeOutput((prev) => {
      const next = [...prev, text];
      if (next.length > MAX_CODE_OUTPUT_LINES) {
        return next.slice(next.length - MAX_CODE_OUTPUT_LINES);
      }
      return next;
    });
  }, []);

  const abortMicRecording = useCallback((discard = true) => {
    if (discard) {
      discardNextMicStopRef.current = true;
    }
    const recorder = mediaRecorderRef.current;
    const stream = mediaStreamRef.current;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        // ignore
      }
    }
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
    }
    mediaRecorderRef.current = null;
    mediaStreamRef.current = null;
    if (discard) {
      audioChunksRef.current = [];
    }
    // If native mic is active, tell backend to stop
    if (!experimentalMicUpload && micStateRef.current === "listening") {
      bridgeRef.current?.sendAction("mic_stop");
    }
    if (micSubmitTimeoutRef.current) {
      clearTimeout(micSubmitTimeoutRef.current);
      micSubmitTimeoutRef.current = null;
    }
    if (micStateRef.current === "listening" || micStateRef.current === "requesting_permission") {
      setMicState("idle");
      setMicError("");
      setMicStageMessage("");
    }
  }, []);

  const startMicRecording = useCallback(async () => {
    if (micStateRef.current !== "idle" && micStateRef.current !== "error") return;
    setMicState("requesting_permission");
    setMicError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      const chosenMime = chooseMimeType();
      const recorder = chosenMime
        ? new MediaRecorder(stream, { mimeType: chosenMime })
        : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) {
          audioChunksRef.current.push(ev.data);
        }
      };

      recorder.onstop = async () => {
        if (discardNextMicStopRef.current) {
          discardNextMicStopRef.current = false;
          audioChunksRef.current = [];
          return;
        }
        const chunks = audioChunksRef.current;
        audioChunksRef.current = [];
        if (chunks.length === 0) {
          setMicState("error");
          setMicError("No audio captured");
          return;
        }
        setMicState("transcribing");
        setMicStageMessage("Encoding audio...");
        try {
          const blobType = chosenMime || recorder.mimeType || "audio/webm";
          const blob = new Blob(chunks, { type: blobType });
          const base64 = await blobToBase64(blob);
          const format = formatFromMimeType(blobType);
          appendActivity(`Mic audio encoded: format=${format}, base64 length=${base64.length}`);
          setMicStageMessage("Sending audio...");
          const sent = bridgeRef.current?.sendAction("mic_audio_submit", {
            audio: base64,
            format,
            sample_rate_hint: 48000,
          });
          if (!sent) {
            setMicState("error");
            setMicError("Failed to send audio to backend");
            return;
          }
          setMicStageMessage("Waiting for backend...");
          // Local timeout: if backend doesn't respond within 10s, show error
          if (micSubmitTimeoutRef.current) clearTimeout(micSubmitTimeoutRef.current);
          micSubmitTimeoutRef.current = setTimeout(() => {
            if (micStateRef.current === "transcribing") {
              setMicState("error");
              setMicError("Backend did not acknowledge mic audio");
            }
          }, 10000);
        } catch {
          setMicState("error");
          setMicError("Failed to encode audio");
        }
      };

      recorder.onerror = () => {
        discardNextMicStopRef.current = true;
        abortMicRecording(true);
        setMicState("error");
        setMicError("Recording error");
      };

      recorder.start();
      setMicState("listening");
    } catch {
      abortMicRecording(true);
      setMicState("error");
      setMicError("Microphone permission denied or unavailable");
    }
  }, [abortMicRecording]);

  const stopMicRecording = useCallback(() => {
    if (micStateRef.current !== "listening") return;
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state === "recording") {
      try {
        recorder.stop();
      } catch {
        // ignore
      }
    }
    const stream = mediaStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
    }
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    setMicState("transcribing");
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
          setMessages(
            syncMessages.map((m) => ({
              id: generateId(),
              role: m.role as ChatMessage["role"],
              content: m.content,
              streaming: false,
            }))
          );
          break;
        }

        case "stream.start": {
          flushPendingDeltas();
          streamingRef.current = true;
          setIsGenerating(true);
          clearThinkingPlaceholders();
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant" && last.streaming) {
              next[next.length - 1] = {
                ...last,
                id: generateId(),
                content: "",
                streaming: true,
              };
            } else {
              next.push({
                id: generateId(),
                role: "assistant",
                content: "",
                streaming: true,
              });
            }
            return next;
          });
          break;
        }

        case "stream.delta": {
          const text = String((payload as { text?: string }).text || "");
          if (!text) break;
          if (!streamingRef.current) {
            streamingRef.current = true;
            setIsGenerating(true);
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
          setIsGenerating(false);
          setStatusText("Idle");
          setModeText("");
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
          setStatusText(sanitizeOperationalText(String((payload as { text?: string }).text || "IDLE")) || "IDLE");
          break;
        }

        case "status.mode": {
          const cleanMode = sanitizeOperationalText(String((payload as { text?: string }).text || ""));
          const upperMode = cleanMode.toUpperCase();
          setModeText(upperMode === "IDLE" || upperMode === "READY" ? "" : cleanMode);
          break;
        }



        case "activity.append": {
          appendActivity(String((payload as { text?: string }).text || ""));
          break;
        }

        case "boot.log": {
          const bootText = String((payload as { text?: string }).text || "");
          appendLog(`[Boot] ${bootText}`);
          handleBootLog(bootText);
          break;
        }

        case "boot.ready": {
          appendLog("[Boot] Ready");
          handleBootReady();
          break;
        }

        case "log.agent": {
          appendLog(`[Agent] ${String((payload as { text?: string }).text || "")}`);
          break;
        }

        case "error": {
          streamingRef.current = false;
          setIsGenerating(false);
          appendActivity(
            `[Error] ${String((payload as { message?: string }).message || "Unknown error")}`
          );
          if (!isOperational) {
            handleBootProgress("Error", "error");
          }
          break;
        }

        // Code session events
        case "code.launch": {
          const p = payload as { path?: string };
          setCodeStatus("launched");
          if (p.path) {
            setCodePathInput(p.path);
            workspace.openFile(p.path, "code");
          }
          workspace.setCodeRunning(true);
          setWorkspaceOpen(true);
          break;
        }

        case "code.reset": {
          setCodeOutput([]);
          workspace.clearCodeOutput();
          break;
        }

        case "code.output": {
          const text = String((payload as { text?: string }).text || "");
          if (text) {
            appendCodeOutput(text);
            workspace.appendCodeOutput(text);
          }
          break;
        }

        case "code.status": {
          const statusText = String((payload as { text?: string }).text || "");
          setCodeStatus(statusText);
          workspace.setCodeRunning(
            !statusText.toLowerCase().includes("exited") &&
            !statusText.toLowerCase().includes("stopped")
          );
          break;
        }

        case "code.active": {
          const isActive = Boolean((payload as { active?: boolean }).active);
          setCodeActive(isActive);
          workspace.setCodeRunning(isActive);
          break;
        }

        case "code.focus": {
          codeInputRef.current?.focus();
          break;
        }

        case "code.preview": {
          setCodePreview(String((payload as { text?: string }).text || ""));
          break;
        }

        // Document ingestion events
        case "document.view": {
          const text = String((payload as { text?: string }).text || "");
          setDocumentsView(text);
          break;
        }

        case "document.ingest_active": {
          setDocumentIngestActive(Boolean((payload as { active?: boolean }).active));
          break;
        }

        // Image events
        case "image.show": {
          const p = payload as { caption?: string; path?: string; url?: string };
          const imageUrl = p.url ? `${IMAGE_BASE_URL}${p.url}` : (p.path || "");
          const caption = String(p.caption || "");
          if (imageUrl) {
            setMessages((prev) => [
              ...prev,
              {
                id: generateId(),
                role: "assistant",
                content: caption || "Image",
                imageUrl,
                streaming: false,
              },
            ]);
          }
          break;
        }

        case "workspace.files": {
          const p = payload as { files?: Array<{ name: string; path: string; size: number }>; path?: string };
          workspace.setWorkspaceFiles(p.files || []);
          if (p.path) workspace.setWorkspacePath(p.path);
          break;
        }

        case "file.contents": {
          const p = payload as { path?: string; name?: string; content?: string; error?: string };
          if (p.error) break;
          const name = (p.name || "").toLowerCase();
          const content = String(p.content || "");
          if (name.endsWith(".py")) {
            setCodePreview(content);
            workspace.setCodeContent(content);
          } else if (name.endsWith(".txt") || name.endsWith(".md")) {
            workspace.setTextContent(content);
          }
          break;
        }

        // System / identity events
        case "user.changed": {
          const p = payload as { user_name?: string; user_id?: string };
          const name = p.user_name || p.user_id || "User";
          setUserName(name);
          break;
        }

        case "style.status": {
          const p = payload as { label?: string; name?: string };
          setStyleLabel(String(p.label || p.name || "Default"));
          break;
        }

        case "auth.status": {
          const p = payload as { waiting?: boolean };
          setAuthWaiting(Boolean(p.waiting));
          break;
        }

        case "tts.status": {
          const p = payload as { state?: string };
          const state = String(p.state || "idle") as TtsState;
          setTtsState(["idle", "synthesizing", "playing", "error"].includes(state) ? state : "idle");
          break;
        }

        case "mic.status": {
          const p = payload as { state?: string; error?: string; stage?: string; message?: string };
          const state = String(p.state || "idle");
          // Clear local submit timeout on any backend mic status
          if (micSubmitTimeoutRef.current) {
            clearTimeout(micSubmitTimeoutRef.current);
            micSubmitTimeoutRef.current = null;
          }
          if (state === "listening") {
            setMicState("listening");
            setMicError("");
            setMicStageMessage(p.message || "Listening...");
            appendActivity(`Mic: listening`);
          } else if (state === "transcribing") {
            setMicState("transcribing");
            setMicError("");
            const msg = p.message || p.stage || "Transcribing...";
            setMicStageMessage(msg);
            appendActivity(`Mic: transcribing – ${msg}`);
          } else if (state === "error") {
            setMicState("error");
            setMicError(String(p.error || "Mic error"));
            setMicStageMessage("");
            appendActivity(`Mic: error – ${String(p.error || "Mic error")}`);
          } else {
            setMicState("idle");
            setMicError("");
            setMicStageMessage("");
          }
          break;
        }

        default:
          // Unhandled kinds go to raw inspector only
          break;
      }
    },
    [appendActivity, appendLog, addRawEvent, clearThinkingPlaceholders, flushPendingDeltas, queueDelta, appendCodeOutput, handleBootLog, handleBootReady, handleBootProgress, isOperational]
  );

  useEffect(() => {
    const bridge = new PiperBridge({
      onStateChange: (state) => {
        setConnState(state);
        if (state === "disconnected" || state === "error") {
          abortMicRecording(true);
          streamingRef.current = false;
          setIsGenerating(false);
          setTtsState("idle");
        }
      },
      onFrame: handleFrame,
      onError: (msg) => appendActivity(`[Bridge Error] ${msg}`),
    });
    bridgeRef.current = bridge;
    bridge.connect();
    return () => {
      abortMicRecording(true);
      bridge.disconnect();
      if (deltaFlushTimerRef.current) {
        clearTimeout(deltaFlushTimerRef.current);
      }
    };
  }, [handleFrame, appendActivity, abortMicRecording]);

  const sendAction = useCallback((action: string, payload: Record<string, unknown> = {}) => {
    return bridgeRef.current?.sendAction(action, payload) ?? false;
  }, []);

  // Request workspace file list when empty workspace is shown
  useEffect(() => {
    if (workspaceOpen && workspace.mode === "empty") {
      sendAction("list_workspace_files");
    }
  }, [workspaceOpen, workspace.mode, sendAction]);

  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;
    setInputText("");
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

  const handleCodeSend = useCallback(() => {
    const text = codeInputText.trim();
    if (!text || !codeActive) return;
    setCodeInputText("");
    sendAction("code_send", { text });
  }, [codeInputText, codeActive, sendAction]);

  const handleCodeRun = useCallback((content: string, path: string) => {
    if (!path) return;
    sendAction("code_run", { path, content });
  }, [sendAction]);

  const handleCodeStop = useCallback(() => {
    sendAction("stop");
  }, [sendAction]);

  const handleTextSave = useCallback((content: string, fileName: string) => {
    if (!fileName) return;
    sendAction("save_workspace_file", { path: fileName, content });
  }, [sendAction]);

  const handleAddDocumentPaths = useCallback(() => {
    const input = documentPathInput.trim();
    if (!input) return;
    const paths = input
      .split(/[;\n]+/)
      .map((p) => p.trim())
      .filter((p) => p.length > 0);
    setSelectedDocumentPaths((prev) => [...prev, ...paths]);
    setDocumentPathInput("");
  }, [documentPathInput]);

  const handleIngestSelected = useCallback(() => {
    if (selectedDocumentPaths.length === 0 || documentIngestActive) return;
    sendAction("document_picker_selected", { paths: selectedDocumentPaths });
  }, [selectedDocumentPaths, documentIngestActive, sendAction]);

  const handleClearDocumentSelection = useCallback(() => {
    setSelectedDocumentPaths([]);
  }, []);

  const handleStop = useCallback(() => {
    abortMicRecording(true);
    sendAction("stop");
  }, [abortMicRecording, sendAction]);

  const handleNewSession = useCallback(() => {
    abortMicRecording(true);
    setMessages([]);
    sendAction("new_session");
    resetUI();
    workspace.closeFile();
  }, [abortMicRecording, sendAction, resetUI, workspace]);

  const handleRestart = useCallback(() => {
    abortMicRecording(true);
    sendAction("restart_piper");
  }, [abortMicRecording, sendAction]);

  const toggleRailPanel = useCallback((panel: RailPanelId) => {
    setExpandedRailPanels((prev) => ({
      ...prev,
      [panel]: !prev[panel],
    }));
  }, []);

  const handleMicClick = useCallback(() => {
    if (experimentalMicUpload) {
      if (micState === "listening") {
        stopMicRecording();
      } else {
        startMicRecording();
      }
    } else {
      if (micState === "listening") {
        bridgeRef.current?.sendAction("mic_stop");
      } else if (micState === "idle" || micState === "error") {
        const sent = bridgeRef.current?.sendAction("mic_start");
        if (!sent) {
          setMicState("error");
          setMicError("Failed to start mic");
        }
      }
    }
  }, [experimentalMicUpload, micState, stopMicRecording, startMicRecording]);

  const micButtonLabel =
    micState === "listening"
      ? "STOP"
      : micState === "requesting_permission" || micState === "transcribing"
      ? "..."
      : "MIC";

  const micButtonClass =
    micState === "listening"
      ? "danger mic-listening"
      : micState === "error"
      ? "mic-error"
      : "";

  const micDisabled =
    connState !== "connected" ||
    isGenerating ||
    micState === "requesting_permission" ||
    micState === "transcribing";

  const micStatusText =
    micState === "listening"
      ? "Listening..."
      : micState === "transcribing"
      ? micStageMessage || "Transcribing..."
      : micState === "error"
      ? micError
      : "";

  const isSpeaking = ttsState === "playing";
  const primaryStatusText =
    micState === "listening"
      ? "Listening"
      : micState === "transcribing"
      ? "Transcribing"
      : isSpeaking
      ? "Speaking"
      : isGenerating
      ? "Generating"
      : statusText || "Idle";
  const detailModeText = isSpeaking ? "TTS playing" : sanitizeOperationalText(modeText);

  const avatarState = (() => {
    if (micState === "listening") return "listening";
    if (micState === "transcribing") return "transcribing";
    if (isSpeaking) return "speaking";
    if (isGenerating) return "generating";
    const st = statusText.toLowerCase();
    if (st.includes("thinking") || st.includes("planning")) return "thinking";
    return "idle";
  })() as "idle" | "listening" | "transcribing" | "thinking" | "generating" | "speaking";

  return (
    <div className="app">
      <TopBar
        connState={connState}
        statusText={primaryStatusText}
        modeText={detailModeText}
        onNewSession={handleNewSession}
        onRestart={handleRestart}
        onStop={handleStop}
        onOpenSystem={() => setSystemDrawerOpen(true)}
      />

      <div className="app-body">
        {/* Column 1: Chat — spans both rows */}
        <div className="chat-col">
          {authWaiting && (
            <div className="auth-banner">
              <span className="auth-icon">🔒</span>
              <span className="auth-text">Password required. Type the password below or /cancel.</span>
            </div>
          )}
          {isOperational ? (
            <ChatPanel
              messages={messages}
              inputText={inputText}
              setInputText={setInputText}
              onSend={handleSend}
              onKeyDown={handleKeyDown}
              chatBoxRef={chatBoxRef}
              connState={connState}
              userName={userName}
              authWaiting={authWaiting}
            />
          ) : (
            <OperationScreen steps={steps} message={bootMessage} title="Booting" />
          )}
        </div>

        {/* Column 2, Row 1: Center stage — avatar + mode selector only */}
        <div className="center-stage">
          <AvatarStage state={avatarState} />
          <ModeSelector styleLabel={styleLabel} userName={userName} />
        </div>

        <aside className="right-rail">
          <div
            className={`rail-workspace-toggle ${workspaceOpen ? "active" : ""}`}
            onClick={() => setWorkspaceOpen(!workspaceOpen)}
            role="button"
            tabIndex={0}
          >
            <span className="rail-ws-label">Workspace</span>
            <span className="rail-ws-hint">{workspaceOpen ? "Close" : "Open"}</span>
          </div>
          <RailCard
            title="Capture"
            collapsible
            expanded={expandedRailPanels.capture}
            onToggle={() => toggleRailPanel("capture")}
          >
            <div className="settings-row">
              <label className="setting-label">
                Event Speech
                <select onChange={(e) => sendAction("event_speech_mode", { mode: e.target.value })} disabled={connState !== "connected"} defaultValue="off">
                  {EVENT_SPEECH_MODES.map((m) => (<option key={m} value={m}>{m}</option>))}
                </select>
              </label>
              <label className="setting-label">
                Live Screen
                <select onChange={(e) => sendAction("live_screen_mode", { mode: e.target.value })} disabled={connState !== "connected"} defaultValue="display">
                  {LIVE_SCREEN_MODES.map((m) => (<option key={m} value={m}>{m}</option>))}
                </select>
              </label>
              <label className="setting-label">
                Interval
                <select onChange={(e) => sendAction("live_screen_interval", { interval_s: Number(e.target.value) })} disabled={connState !== "connected"} defaultValue={10}>
                  {LIVE_SCREEN_INTERVALS.map((n) => (<option key={n} value={n}>{n}s</option>))}
                </select>
              </label>
            </div>
          </RailCard>



          <RailCard
            title="Documents"
            collapsible
            expanded={expandedRailPanels.documents}
            onToggle={() => toggleRailPanel("documents")}
            badge={<span className={documentIngestActive ? "rail-badge active" : "rail-badge"}>{documentIngestActive ? "Ingesting..." : "Idle"}</span>}
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
                    <input className="input-text doc-path" type="text" value={documentPathInput} onChange={(e) => setDocumentPathInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleAddDocumentPaths(); } }} placeholder="Path(s) separated by ; or newline..." disabled={connState !== "connected"} />
                    <button onClick={handleAddDocumentPaths} disabled={connState !== "connected" || !documentPathInput.trim()}>Add</button>
                  </div>
                  <div className="doc-control-row">
                    <button onClick={handleIngestSelected} disabled={connState !== "connected" || documentIngestActive || selectedDocumentPaths.length === 0}>Ingest Selected</button>
                    <button onClick={handleClearDocumentSelection} disabled={connState !== "connected"}>Clear</button>
                    <button onClick={() => sendAction("document_picker_cancel")} disabled={connState !== "connected" || !documentIngestActive}>Cancel</button>
                  </div>
                </div>
              </div>
          </RailCard>

          <RailCard
            title="Activity & Logs"
            collapsible
            expanded={expandedRailPanels.activity}
            onToggle={() => toggleRailPanel("activity")}
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
            expanded={expandedRailPanels.raw}
            onToggle={() => toggleRailPanel("raw")}
          >
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

        {/* Columns 2+3, Row 2: Voice strip — always visible */}
        <div className="voice-strip-col">
          <VoiceStrip
            micState={micState}
            micButtonLabel={micButtonLabel}
            micButtonClass={micButtonClass}
            micDisabled={micDisabled}
            micStatusText={micStatusText}
            onMicClick={handleMicClick}
            connState={connState}
            isGenerating={isGenerating}
            isSpeaking={isSpeaking}
          />
        </div>

        {/* Overlay: covers row 1 only, columns 2+3 */}
        {workspaceOpen && (
          <div className="workspace-overlay-full">
            <div className="workspace-overlay-header">
              <span className="workspace-overlay-title">Workspace</span>
              <button
                className="workspace-overlay-close"
                onClick={() => setWorkspaceOpen(false)}
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
                  const file = files[0];
                  if (!file) return;
                  const name = file.name.toLowerCase();
                  if (name.endsWith(".py")) {
                    workspace.openFile(file.name, "code");
                    setCodePathInput(file.name);
                    const reader = new FileReader();
                    reader.onload = (e) => {
                      const content = String(e.target?.result || "");
                      setCodePreview(content);
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
                onClose={() => {
                  workspace.closeFile();
                }}
                codeContent={codePreview}
                onCodeChange={setCodePreview}
                codeOutput={codeOutput}
                codeRunning={codeActive}
                codeStatus={codeStatus}
                codePath={codePathInput}
                onCodePathChange={setCodePathInput}
                onCodeRun={handleCodeRun}
                onCodeStop={handleCodeStop}
                onCodeClear={() => setCodeOutput([])}
                connState={connState}
                stdinText={codeInputText}
                onStdinChange={setCodeInputText}
                onStdinSend={handleCodeSend}
                textContent={workspace.textContent}
                onTextContentChange={workspace.setTextContent}
                onTextSave={handleTextSave}
                imageUrl={workspace.imageUrl}
                visionText={workspace.visionText}
                workspaceFiles={workspace.workspaceFiles}
                workspacePath={workspace.workspacePath}
                onFileFromList={(path) => {
                  const fileName = path.split(/[\\/]/).pop() || path;
                  const name = fileName.toLowerCase();
                  if (name.endsWith(".py")) {
                    workspace.openFile(path, "code");
                    setCodePathInput(fileName);
                    sendAction("read_workspace_file", { path });
                  } else if (name.endsWith(".txt") || name.endsWith(".md")) {
                    workspace.openFile(path, "text");
                    sendAction("read_workspace_file", { path });
                  } else if (/\.(jpg|jpeg|png|webp)$/.test(name)) {
                    workspace.openFile(path, "vision");
                    workspace.setVisionImage(`/images/${fileName}`);
                  }
                }}
              />
            </div>
          </div>
        )}
      </div>

      <StatusFooter statsText="" />

      <SystemDrawer
        isOpen={systemDrawerOpen}
        onClose={() => setSystemDrawerOpen(false)}
        connState={connState}
        ttsState={ttsState}
        logs={logs}
        userName={userName}
        backendVersion="Piper v2.0"
      />
    </div>
  );
}
