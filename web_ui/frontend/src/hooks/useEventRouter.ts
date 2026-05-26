import { useCallback, useRef, useState } from "react";
import type { BackendFrame, ChatMessage, MicStatus, RawEvent, UiError } from "../types";
import type { TtsState } from "../types";
import { generateId, isThinkingPlaceholder, sanitizeOperationalText } from "../utils";

const DELTA_COALESCE_MS = 16;
const MAX_CODE_OUTPUT_LINES = 500;

type DeltaFlushHandle = ReturnType<typeof setTimeout> | number;

interface UseEventRouterOptions {
  // UI setters (from usePiperUI)
  setStatusText: (text: string) => void;
  setModeText: (text: string) => void;
  setUserName: (name: string) => void;
  setStyleLabel: (label: string) => void;
  setAuthWaiting: (waiting: boolean) => void;
  setTtsState: (state: TtsState) => void;
  // Boot callbacks
  onBootLog?: (text: string) => void;
  onBootReady?: () => void;
  onBootProgress?: (label: string, state: "pending" | "done" | "error") => void;
  isOperational?: boolean;
  // Workspace
  workspace: {
    openFile: (path: string, mode: "code" | "text" | "vision") => void;
    closeFile: () => void;
    setCodeRunning: (running: boolean) => void;
    clearCodeOutput: () => void;
    appendCodeOutput: (text: string) => void;
    setCodeContent: (content: string) => void;
    setTextContent: (content: string) => void;
    setWorkspaceFiles: (files: Array<{ name: string; path: string; size: number }>) => void;
    setWorkspacePath: (path: string) => void;
    setVisionImage: (url: string) => void;
  };
  setWorkspaceOpen: (open: boolean) => void;
}

export function useEventRouter({
  setStatusText,
  setModeText,
  setUserName,
  setStyleLabel,
  setAuthWaiting,
  setTtsState,
  onBootLog,
  onBootReady,
  onBootProgress,
  isOperational = false,
  workspace,
  setWorkspaceOpen,
}: UseEventRouterOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);

  const [activities, setActivities] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [rawEvents, setRawEvents] = useState<RawEvent[]>([]);
  const [errors, setErrors] = useState<UiError[]>([]);

  // Code session state
  const [codeOutput, setCodeOutput] = useState<string[]>([]);
  const [codeStatus, setCodeStatus] = useState("idle");
  const [codeActive, setCodeActive] = useState(false);
  const [codePreview, setCodePreview] = useState("");
  const [codePathInput, setCodePathInput] = useState("");

  // Document ingestion state
  const [documentsView, setDocumentsView] = useState("");
  const [documentIngestActive, setDocumentIngestActive] = useState(false);
  const [selectedDocumentPaths, setSelectedDocumentPaths] = useState<string[]>([]);
  const [micStatus, setMicStatus] = useState<MicStatus>({ state: "idle" });

  const streamingRef = useRef(false);
  const pendingDeltasRef = useRef("");
  const deltaFlushHandleRef = useRef<DeltaFlushHandle | null>(null);
  const deltaFlushModeRef = useRef<"raf" | "timeout" | null>(null);
  const suppressStreamRef = useRef(false);

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

  const addError = useCallback((message: string, sourceKind: string, kind: string) => {
    setErrors((prev) => [
      ...prev.slice(-199),
      {
        id: generateId(),
        message,
        sourceKind,
        kind,
        receivedAt: Date.now(),
      },
    ]);
  }, []);

  const getEventPayload = (frame: BackendFrame) => frame.payload;

  const getErrorMessage = (frame: BackendFrame, payload: Record<string, unknown>) => {
    if (frame.frame === "error") {
      return String(frame.message || "Unknown error");
    }
    return String((payload as { message?: string }).message || "Unknown error");
  };

  const getDeltaText = (payload: Record<string, unknown>) =>
    String((payload as { text?: string }).text || "");

  const flushPendingDeltas = useCallback(() => {
    const text = pendingDeltasRef.current;
    pendingDeltasRef.current = "";
    if (deltaFlushHandleRef.current !== null) {
      if (deltaFlushModeRef.current === "raf" && typeof window !== "undefined" && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(deltaFlushHandleRef.current as number);
      } else {
        clearTimeout(deltaFlushHandleRef.current as ReturnType<typeof setTimeout>);
      }
      deltaFlushHandleRef.current = null;
      deltaFlushModeRef.current = null;
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

  const scheduleDeltaFlush = useCallback(() => {
    if (deltaFlushHandleRef.current !== null) return;
    if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
      deltaFlushModeRef.current = "raf";
      deltaFlushHandleRef.current = window.requestAnimationFrame(() => {
        deltaFlushHandleRef.current = null;
        deltaFlushModeRef.current = null;
        flushPendingDeltas();
      });
      return;
    }
    deltaFlushModeRef.current = "timeout";
    deltaFlushHandleRef.current = setTimeout(() => {
      deltaFlushHandleRef.current = null;
      deltaFlushModeRef.current = null;
      flushPendingDeltas();
    }, DELTA_COALESCE_MS);
  }, [flushPendingDeltas]);

  const queueDelta = useCallback(
    (text: string) => {
      pendingDeltasRef.current += text;
      scheduleDeltaFlush();
    },
    [scheduleDeltaFlush]
  );

  const clearThinkingPlaceholders = useCallback(() => {
    setMessages((prev) => prev.filter((m) => !isThinkingPlaceholder(m)));
  }, []);

  const settleStreaming = useCallback(() => {
    flushPendingDeltas();
    if (deltaFlushHandleRef.current !== null) {
      if (deltaFlushModeRef.current === "raf" && typeof window !== "undefined" && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(deltaFlushHandleRef.current as number);
      } else {
        clearTimeout(deltaFlushHandleRef.current as ReturnType<typeof setTimeout>);
      }
      deltaFlushHandleRef.current = null;
      deltaFlushModeRef.current = null;
    }
    pendingDeltasRef.current = "";
    streamingRef.current = false;
    setIsGenerating(false);
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        if (String(last.content || "").trim()) {
          next[next.length - 1] = { ...last, streaming: false };
        } else {
          next.pop();
        }
      }
      return next;
    });
  }, [flushPendingDeltas]);

  const stopStreamingLocally = useCallback(() => {
    suppressStreamRef.current = true;
    settleStreaming();
  }, [settleStreaming]);

  const clearStreamSuppression = useCallback(() => {
    suppressStreamRef.current = false;
  }, []);

  const ensureAssistantStreamMessage = useCallback(() => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        return next;
      }
      next.push({
        id: generateId(),
        role: "assistant",
        content: "",
        streaming: true,
      });
      return next;
    });
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

  const reset = useCallback(() => {
    setMessages([]);
    setIsGenerating(false);
    setActivities([]);
    setLogs([]);
    setRawEvents([]);
    setCodeOutput([]);
    setCodeStatus("idle");
    setCodeActive(false);
    setCodePreview("");
    setCodePathInput("");
    setDocumentsView("");
    setDocumentIngestActive(false);
    setSelectedDocumentPaths([]);
    setMicStatus({ state: "idle" });
    setErrors([]);
    streamingRef.current = false;
    pendingDeltasRef.current = "";
    suppressStreamRef.current = false;
    if (deltaFlushHandleRef.current !== null) {
      if (deltaFlushModeRef.current === "raf" && typeof window !== "undefined" && window.cancelAnimationFrame) {
        window.cancelAnimationFrame(deltaFlushHandleRef.current as number);
      } else {
        clearTimeout(deltaFlushHandleRef.current as ReturnType<typeof setTimeout>);
      }
      deltaFlushHandleRef.current = null;
      deltaFlushModeRef.current = null;
    }
  }, []);

  const handleFrame = useCallback(
    (frame: BackendFrame) => {
      if (frame.frame === "error") {
        suppressStreamRef.current = false;
        addRawEvent(frame);
        const message = getErrorMessage(frame, getEventPayload(frame));
        appendActivity(`[Error] ${message}`);
        addError(message, "", frame.kind);
        setStatusText("Idle");
        setModeText("");
        settleStreaming();
        return;
      }

      const payload = getEventPayload(frame);
      const { kind } = frame;
      addRawEvent(frame);

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
          if (suppressStreamRef.current) break;
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
          if (suppressStreamRef.current) break;
          const text = getDeltaText(payload);
          if (!text) break;
          if (!streamingRef.current) {
            streamingRef.current = true;
            setIsGenerating(true);
            clearThinkingPlaceholders();
            ensureAssistantStreamMessage();
          }
          queueDelta(text);
          break;
        }

        case "stream.end": {
          suppressStreamRef.current = false;
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
          onBootLog?.(bootText);
          break;
        }

        case "boot.ready": {
          appendLog("[Boot] Ready");
          onBootReady?.();
          break;
        }

        case "log.agent": {
          appendLog(`[Agent] ${String((payload as { text?: string }).text || "")}`);
          break;
        }

        case "error": {
          const message = getErrorMessage(frame, payload);
          streamingRef.current = false;
          setIsGenerating(false);
          appendActivity(`[Error] ${message}`);
          addError(message, String((frame as { sourceKind?: string }).sourceKind || ""), kind);
          if (!isOperational) {
            onBootProgress?.("Error", "error");
          }
          break;
        }

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
          const st = String((payload as { text?: string }).text || "");
          setCodeStatus(st);
          workspace.setCodeRunning(
            !st.toLowerCase().includes("exited") && !st.toLowerCase().includes("stopped")
          );
          break;
        }

        case "code.active": {
          const isActive = Boolean((payload as { active?: boolean }).active);
          setCodeActive(isActive);
          workspace.setCodeRunning(isActive);
          break;
        }

        case "code.preview": {
          setCodePreview(String((payload as { text?: string }).text || ""));
          break;
        }

        case "document.view": {
          setDocumentsView(String((payload as { text?: string }).text || ""));
          break;
        }

        case "document.ingest_active": {
          setDocumentIngestActive(Boolean((payload as { active?: boolean }).active));
          break;
        }

        case "image.show": {
          const p = payload as { caption?: string; path?: string; url?: string };
          const imageUrl = p.url || p.path || "";
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

        case "user.changed": {
          const p = payload as { user_name?: string; user_id?: string };
          setUserName(p.user_name || p.user_id || "User");
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
          const p = payload as {
            state?: string;
            stage?: string;
            message?: string;
            error?: string;
          };
          const state = ["idle", "listening", "transcribing", "error"].includes(String(p.state))
            ? (String(p.state) as MicStatus["state"])
            : "idle";
          const next: MicStatus = { state };
          if (p.stage) next.stage = String(p.stage);
          if (p.message) next.message = String(p.message);
          if (p.error) next.error = String(p.error);
          setMicStatus(next);
          appendActivity(
            `Mic status: ${state}${p.stage ? `/${p.stage}` : ""}${p.message ? ` - ${p.message}` : ""}${p.error ? ` (${p.error})` : ""}`
          );
          break;
        }

        default:
          break;
      }
    },
    [setStatusText, setModeText, setUserName, setStyleLabel, setAuthWaiting, setTtsState, appendActivity, appendLog, addRawEvent, addError, clearThinkingPlaceholders, ensureAssistantStreamMessage, flushPendingDeltas, queueDelta, appendCodeOutput, onBootLog, onBootReady, onBootProgress, isOperational, workspace, setWorkspaceOpen]
  );

  return {
    messages,
    setMessages,
    isGenerating,
    activities,
    logs,
    rawEvents,
    errors,
    codeOutput,
    codeStatus,
    codeActive,
    codePreview,
    codePathInput,
    setCodePreview,
    setCodePathInput,
    setCodeOutput,
    documentsView,
    documentIngestActive,
    selectedDocumentPaths,
    setSelectedDocumentPaths,
    micStatus,
    handleFrame,
    appendActivity,
    appendLog,
    flushPendingDeltas,
    settleStreaming,
    stopStreamingLocally,
    clearStreamSuppression,
    reset,
  };
}
