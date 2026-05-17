import { useCallback, useEffect, useRef, useState } from "react";
import { PiperBridge, WS_URL } from "./bridge";
import type { BackendFrame, ChatMessage, ConnectionState, RawEvent } from "./types";
import TopBar from "./components/TopBar";
import ChatPanel from "./components/ChatPanel";
import AvatarStage from "./components/AvatarStage";
import ModeSelector from "./components/ModeSelector";
import VoiceStrip from "./components/VoiceStrip";
import StatusFooter from "./components/StatusFooter";

const EVENT_SPEECH_MODES = ["off", "noisy", "all"];
const LIVE_SCREEN_MODES = ["display", "window", "pointer"];
const LIVE_SCREEN_INTERVALS = [2, 5, 10, 15];
const DELTA_COALESCE_MS = 16;
const MAX_CODE_OUTPUT_LINES = 500;

type MicState = "idle" | "requesting_permission" | "listening" | "transcribing" | "error";

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
  const [connState, setConnState] = useState<ConnectionState>("disconnected");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [statusText, setStatusText] = useState("IDLE");
  const [modeText, setModeText] = useState("");
  const [stepText, setStepText] = useState("");
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
  const [imageCaption, setImageCaption] = useState("");
  const [imagePath, setImagePath] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [visionNotes, setVisionNotes] = useState<string[]>([]);
  const [imageLoadError, setImageLoadError] = useState(false);

  // System / identity state
  const [activeUserLabel, setActiveUserLabel] = useState("");
  const [identityStatus, setIdentityStatus] = useState("");
  const [statsText, setStatsText] = useState("");
  const [configReloads, setConfigReloads] = useState<string[]>([]);
  const [controlsRefreshCount, setControlsRefreshCount] = useState(0);
  const [lastControlsRefreshAt, setLastControlsRefreshAt] = useState("");
  const [lastStatsRefreshAt, setLastStatsRefreshAt] = useState("");

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
  const codeOutputRef = useRef<HTMLDivElement | null>(null);
  const codeInputRef = useRef<HTMLInputElement | null>(null);
  const documentsViewRef = useRef<HTMLDivElement | null>(null);
  const visionNotesRef = useRef<HTMLDivElement | null>(null);
  const pendingDeltasRef = useRef("");
  const deltaFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-scroll chat to bottom when messages change
  useEffect(() => {
    const el = chatBoxRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  // Auto-scroll code output to bottom
  useEffect(() => {
    const el = codeOutputRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [codeOutput]);

  // Auto-scroll documents view to bottom
  useEffect(() => {
    const el = documentsViewRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [documentsView]);

  // Auto-scroll vision notes to bottom
  useEffect(() => {
    const el = visionNotesRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [visionNotes]);

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

        // Code session events
        case "code.launch": {
          const p = payload as { path?: string };
          setCodeStatus("launched");
          if (p.path) {
            setCodePathInput(p.path);
          }
          break;
        }

        case "code.reset": {
          setCodeOutput([]);
          setCodePreview("");
          break;
        }

        case "code.output": {
          const text = String((payload as { text?: string }).text || "");
          if (text) appendCodeOutput(text);
          break;
        }

        case "code.status": {
          setCodeStatus(String((payload as { text?: string }).text || ""));
          break;
        }

        case "code.active": {
          setCodeActive(Boolean((payload as { active?: boolean }).active));
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

        // Image / vision events
        case "image.show": {
          const p = payload as { caption?: string; path?: string; url?: string };
          setImageCaption(String(p.caption || ""));
          setImagePath(String(p.path || ""));
          setImageLoadError(false);
          if (p.url) {
            setImageUrl(`${IMAGE_BASE_URL}${p.url}`);
          } else {
            setImageUrl("");
          }
          break;
        }

        case "vision.note": {
          const p = payload as { text?: string; speak?: boolean };
          const note = String(p.text || "");
          if (note) {
            setVisionNotes((prev) => {
              const next = [...prev, note];
              return next.slice(-100);
            });
          }
          break;
        }

        // System / identity events
        case "user.changed": {
          const p = payload as { preserve_transcript?: boolean };
          setActiveUserLabel("Active user changed");
          setIdentityStatus(p.preserve_transcript ? "Transcript preserved" : "Transcript reset");
          break;
        }

        case "stats.refresh": {
          const p = payload as { text?: string };
          setStatsText(String(p.text || "Stats refreshed"));
          setLastStatsRefreshAt(new Date().toLocaleTimeString());
          break;
        }

        case "config.reloaded": {
          const p = payload as { changed_keys?: string[] };
          const keys = p.changed_keys || [];
          if (keys.length > 0) {
            setConfigReloads((prev) => {
              const entry = `${new Date().toLocaleTimeString()}: ${keys.join(", ")}`;
              const next = [...prev, entry];
              return next.slice(-50);
            });
          }
          break;
        }

        case "controls.refresh": {
          setControlsRefreshCount((c) => c + 1);
          setLastControlsRefreshAt(new Date().toLocaleTimeString());
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
    [appendActivity, appendLog, addRawEvent, clearThinkingPlaceholders, flushPendingDeltas, queueDelta, appendCodeOutput]
  );

  useEffect(() => {
    const bridge = new PiperBridge({
      onStateChange: (state) => {
        setConnState(state);
        if (state === "disconnected" || state === "error") {
          abortMicRecording(true);
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

  const handleCodeKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleCodeSend();
      }
    },
    [handleCodeSend]
  );

  const handleCodeRun = useCallback(() => {
    const path = codePathInput.trim();
    if (!path) return;
    sendAction("code_run", { path });
  }, [codePathInput, sendAction]);

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

  const handleClearVisionNotes = useCallback(() => {
    setVisionNotes([]);
  }, []);

  const handleClearConfigReloads = useCallback(() => {
    setConfigReloads([]);
  }, []);

  const handleClearStats = useCallback(() => {
    setStatsText("");
    setLastStatsRefreshAt("");
  }, []);

  const handleStop = useCallback(() => {
    abortMicRecording(true);
    sendAction("stop");
  }, [abortMicRecording, sendAction]);

  const handleNewSession = useCallback(() => {
    abortMicRecording(true);
    setMessages([]);
    sendAction("new_session");
  }, [abortMicRecording, sendAction]);

  const handleRestart = useCallback(() => {
    abortMicRecording(true);
    sendAction("restart_piper");
  }, [abortMicRecording, sendAction]);

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
    streamingRef.current ||
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

  const avatarState = (() => {
    if (micState === "listening") return "listening";
    if (micState === "transcribing") return "transcribing";
    if (streamingRef.current) return "speaking";
    const st = statusText.toLowerCase();
    if (st.includes("thinking") || st.includes("planning")) return "thinking";
    return "idle";
  })() as "idle" | "listening" | "transcribing" | "thinking" | "speaking";

  return (
    <div className="app">
      <TopBar
        connState={connState}
        statusText={statusText}
        modeText={modeText}
        onNewSession={handleNewSession}
        onRestart={handleRestart}
        onStop={handleStop}
      />

      <div className="app-body">
        <ChatPanel
          messages={messages}
          inputText={inputText}
          setInputText={setInputText}
          onSend={handleSend}
          onKeyDown={handleKeyDown}
          chatBoxRef={chatBoxRef}
          connState={connState}
        />

        <div className="center-stage">
          <AvatarStage state={avatarState} />
          <ModeSelector />
        </div>

        <aside className="right-rail">
          {/* Quick Actions */}
          <div className="rail-card compact">
            <div className="rail-card-header">
              <h3>Quick Actions</h3>
            </div>
            <div className="rail-card-body">
              <div className="quick-actions-grid">
                <button className="action-btn" onClick={handleStop} disabled={connState !== "connected"}>Stop</button>
                <button className="action-btn" onClick={handleNewSession} disabled={connState !== "connected"}>New Session</button>
                <button className="action-btn danger" onClick={handleRestart} disabled={connState !== "connected"}>Restart</button>
              </div>
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
            </div>
          </div>

          {/* Status */}
          <div className="rail-card compact">
            <div className="rail-card-header">
              <h3>Status</h3>
            </div>
            <div className="rail-card-body">
              <div className="status-pills">
                <div className="status-pill">{statusText}</div>
                {modeText && <div className="status-pill mode">{modeText}</div>}
                {stepText && <div className="status-pill step">{stepText}</div>}
              </div>
            </div>
          </div>

          {/* Code Session */}
          <div className="rail-card">
            <div className="rail-card-header">
              <h3>Code Session</h3>
              <span className={codeActive ? "rail-badge active" : codeStatus.includes("error") || codeStatus.includes("fail") ? "rail-badge error" : "rail-badge"}>{codeStatus}</span>
            </div>
            <div className="rail-card-body">
              <div className="code-panel">
                {codePreview && (
                  <div className="code-preview">
                    <pre>{codePreview}</pre>
                  </div>
                )}
                <div className="code-output" ref={codeOutputRef}>
                  {codeOutput.map((line, i) => (
                    <div key={`c-${i}`} className="code-line">{line}</div>
                  ))}
                </div>
                <div className="code-controls">
                  <div className="code-control-row">
                    <input className="input-text code-path" type="text" value={codePathInput} onChange={(e) => setCodePathInput(e.target.value)} placeholder="Script path..." disabled={connState !== "connected"} />
                    <button onClick={handleCodeRun} disabled={connState !== "connected" || !codePathInput.trim()}>Run</button>
                  </div>
                  <div className="code-control-row">
                    <input ref={codeInputRef} className="input-text" type="text" value={codeInputText} onChange={(e) => setCodeInputText(e.target.value)} onKeyDown={handleCodeKeyDown} placeholder="Stdin..." disabled={connState !== "connected" || !codeActive} />
                    <button onClick={handleCodeSend} disabled={connState !== "connected" || !codeActive || !codeInputText.trim()}>Send</button>
                    <button onClick={() => sendAction("code_clear")} disabled={connState !== "connected"}>Clear</button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Image / Vision */}
          <div className="rail-card">
            <div className="rail-card-header">
              <h3>Image / Vision</h3>
            </div>
            <div className="rail-card-body">
              <div className="image-panel">
                <div className="image-preview-area">
                  {imageUrl && !imageLoadError ? (
                    <img src={imageUrl} alt={imageCaption || "Generated image"} className="image-preview-img" onError={() => setImageLoadError(true)} onLoad={() => setImageLoadError(false)} />
                  ) : imagePath ? (
                    <div className="image-meta">
                      <div className="image-meta-caption">{imageCaption}</div>
                      <div className="image-meta-path">{imagePath}</div>
                      {imageLoadError && <div className="image-meta-hint">Image preview unavailable. Ensure the backend is serving static files.</div>}
                    </div>
                  ) : (
                    <div className="image-meta"><div className="image-meta-hint">No image yet.</div></div>
                  )}
                </div>
                <div className="vision-notes" ref={visionNotesRef}>
                  {visionNotes.map((note, i) => (
                    <div key={`v-${i}`} className="vision-note">{note}</div>
                  ))}
                </div>
                <div className="image-controls">
                  <button onClick={handleClearVisionNotes} disabled={connState !== "connected" || visionNotes.length === 0}>Clear Notes</button>
                </div>
              </div>
            </div>
          </div>

          {/* Documents */}
          <div className="rail-card">
            <div className="rail-card-header">
              <h3>Documents</h3>
              <span className={documentIngestActive ? "rail-badge active" : "rail-badge"}>{documentIngestActive ? "Ingesting..." : "Idle"}</span>
            </div>
            <div className="rail-card-body">
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
            </div>
          </div>

          {/* System Overview */}
          <div className="rail-card compact">
            <div className="rail-card-header">
              <h3>System Overview</h3>
            </div>
            <div className="rail-card-body">
              <div className="sys-panel">
                <div className="sys-block">
                  <div className="sys-label">Identity</div>
                  <div className="sys-value">{activeUserLabel || "—"} {identityStatus && <span className="sys-sub">({identityStatus})</span>}</div>
                </div>
                <div className="sys-block">
                  <div className="sys-label">Stats</div>
                  <div className="sys-value">{statsText || "—"}</div>
                  {lastStatsRefreshAt && <div className="sys-sub">Refreshed: {lastStatsRefreshAt}</div>}
                </div>
                <div className="sys-block">
                  <div className="sys-label">Controls Refresh</div>
                  <div className="sys-value">{controlsRefreshCount} events</div>
                  {lastControlsRefreshAt && <div className="sys-sub">Last: {lastControlsRefreshAt}</div>}
                </div>
                {configReloads.length > 0 && (
                  <div className="sys-block">
                    <div className="sys-label">Config Reloads</div>
                    <div className="sys-list">
                      {configReloads.map((entry, i) => (
                        <div key={`cr-${i}`} className="sys-list-item">{entry}</div>
                      ))}
                    </div>
                  </div>
                )}
                <div className="sys-controls">
                  <button onClick={handleClearStats} disabled={!statsText && !lastStatsRefreshAt}>Clear Stats</button>
                  <button onClick={handleClearConfigReloads} disabled={configReloads.length === 0}>Clear Config Log</button>
                </div>
              </div>
            </div>
          </div>

          {/* Activity & Logs */}
          <div className="rail-card collapsible">
            <div className="rail-card-header">
              <h3>Activity & Logs</h3>
            </div>
            <div className="rail-card-body">
              <div className="log-box">
                {activities.map((a, i) => (
                  <div key={`a-${i}`} className="log-line activity">{a}</div>
                ))}
                {logs.map((l, i) => (
                  <div key={`l-${i}`} className="log-line log">{l}</div>
                ))}
              </div>
            </div>
          </div>

          {/* Raw Events */}
          <div className="rail-card collapsible">
            <div className="rail-card-header">
              <h3>Raw Events</h3>
            </div>
            <div className="rail-card-body">
              <div className="log-box raw">
                {rawEvents.map((e, i) => (
                  <details key={`e-${i}`} className="raw-event">
                    <summary>{e.kind} ({e.sourceKind})</summary>
                    <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                  </details>
                ))}
              </div>
            </div>
          </div>
        </aside>
      </div>

      <VoiceStrip
        micState={micState}
        micButtonLabel={micButtonLabel}
        micButtonClass={micButtonClass}
        micDisabled={micDisabled}
        micStatusText={micStatusText}
        onMicClick={handleMicClick}
        connState={connState}
        isSpeaking={streamingRef.current}
      />

      <StatusFooter statsText={statsText} modeText={modeText} />
    </div>
  );
}
