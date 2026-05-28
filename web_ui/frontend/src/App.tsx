import { useCallback, useEffect, useRef, useState } from "react";
import { PiperBridge, WS_URL } from "./bridge";
import type { ConnectionState, RailPanelId } from "./types";
import TopBar from "./components/TopBar";
import ChatPanel from "./components/ChatPanel";
import AvatarStage from "./components/AvatarStage";
import ModeSelector from "./components/ModeSelector";
import VoiceStrip from "./components/VoiceStrip";
import StatusFooter from "./components/StatusFooter";
import OperationScreen from "./components/OperationScreen";
import SystemDrawer from "./components/SystemDrawer";
import RightRail from "./components/RightRail";
import WorkspaceOverlay from "./components/WorkspaceOverlay";
import { useOperationMode } from "./hooks/useOperationMode";
import { usePiperUI } from "./hooks/usePiperUI";
import { useWorkspace } from "./hooks/useWorkspace";
import { useEventRouter } from "./hooks/useEventRouter";
import { useMic } from "./hooks/useMic";
import { sanitizeOperationalText } from "./utils";

export type { RailPanelId };

const IMAGE_BASE_URL = WS_URL.replace(/^ws:\/\//, "http://").replace(/\/ws$/, "");

export function workspaceImageUrl(filePath: string, workspacePath: string): string {
  const normalizedFile = filePath.replace(/\\/g, "/");
  const normalizedWorkspace = workspacePath.replace(/\\/g, "/");
  if (normalizedWorkspace && normalizedFile.startsWith(normalizedWorkspace + "/")) {
    const relative = normalizedFile.slice(normalizedWorkspace.length + 1);
    const encodedRelative = relative
      .split("/")
      .map(encodeURIComponent)
      .join("/");
    return `${IMAGE_BASE_URL}/workspace/${encodedRelative}`;
  }
  const basename = normalizedFile.split("/").pop() || normalizedFile;
  return `${IMAGE_BASE_URL}/images/${encodeURIComponent(basename)}`;
}

export default function App() {
  const { steps, bootMessage, handleBootLog, handleBootReady, handleBootProgress, isOperational } =
    useOperationMode();

  const [connState, setConnState] = useState<ConnectionState>("disconnected");

  // Refs first (needed by hooks below)
  const bridgeRef = useRef<PiperBridge | null>(null);
  const chatBoxRef = useRef<HTMLDivElement | null>(null);
  const documentsViewRef = useRef<HTMLDivElement | null>(null);

  const ui = usePiperUI();
  const {
    mode: modeText,
    statusText,
    styleLabel,
    userName,
    authWaiting,
    ttsState,
    workspaceOpen,
    setWorkspaceOpen,
    setStatusText,
    setMode: setModeText,
    setUserName,
    setStyleLabel,
    setAuthWaiting,
    setTtsState,
    resetUI,
  } = ui;

  const workspace = useWorkspace();

  const router = useEventRouter({
    setStatusText,
    setModeText,
    setUserName,
    setStyleLabel,
    setAuthWaiting,
    setTtsState,
    onBootLog: handleBootLog,
    onBootReady: handleBootReady,
    onBootProgress: handleBootProgress,
    isOperational,
    workspace: {
      openFile: workspace.openFile,
      closeFile: workspace.closeFile,
      setCodeRunning: workspace.setCodeRunning,
      clearCodeOutput: workspace.clearCodeOutput,
      appendCodeOutput: workspace.appendCodeOutput,
      setCodeContent: workspace.setCodeContent,
      setTextContent: workspace.setTextContent,
      setWorkspaceFiles: workspace.setWorkspaceFiles,
      setWorkspacePath: workspace.setWorkspacePath,
      setVisionImage: workspace.setVisionImage,
    },
    setWorkspaceOpen,
  });

  const {
    messages,
    setMessages,
    isGenerating,
    activities,
    logs,
    filteredRawEvents,
    rawEventFilter,
    setRawEventFilter,
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
    liveScreen,
    stats,
    handleFrame,
    appendActivity,
    stopStreamingLocally,
    clearStreamSuppression,
    reset: resetRouter,
  } = router;

  const mic = useMic({
    bridgeRef,
    appendActivity,
  });
  const {
    micState,
    startMicRecording,
    stopMicRecording,
    abortMicRecording,
    handleBackendMicStatus,
    micButtonLabel,
    micButtonClass,
    micStatusText,
  } = mic;

  // Stable refs for bridge callbacks — prevent infinite re-registrations.
  const abortMicRef = useRef(abortMicRecording);
  const handleFrameRef = useRef(handleFrame);
  const appendActivityRef = useRef(appendActivity);
  const setTtsStateRef = useRef(setTtsState);
  abortMicRef.current = abortMicRecording;
  handleFrameRef.current = handleFrame;
  appendActivityRef.current = appendActivity;
  setTtsStateRef.current = setTtsState;

  const [inputText, setInputText] = useState("");
  const [codeInputText, setCodeInputText] = useState("");
  const [documentPathInput, setDocumentPathInput] = useState("");

  const [expandedRailPanels, setExpandedRailPanels] = useState<Record<RailPanelId, boolean>>({
    code: false,
    documents: false,
    system: false,
    activity: false,
    raw: false,
    capture: false,
    liveScreen: false,
    stats: false,
  });

  const [systemDrawerOpen, setSystemDrawerOpen] = useState(false);

  // Auto-scroll chat to bottom when messages change
  useEffect(() => {
    const el = chatBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Auto-scroll documents view to bottom
  useEffect(() => {
    const el = documentsViewRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [documentsView]);

  // Backend mic.status acknowledgement clears local transcribing state
  useEffect(() => {
    handleBackendMicStatus(micStatus);
  }, [micStatus, handleBackendMicStatus]);

  // Bridge setup — empty deps: refs keep callbacks fresh without re-connects.
  useEffect(() => {
    const bridge = new PiperBridge({
      onStateChange: (state) => {
        setConnState(state);
        if (state === "disconnected" || state === "error") {
          abortMicRef.current(true);
          setTtsStateRef.current("idle");
        }
      },
      onFrame: (frame) => handleFrameRef.current(frame),
      onError: (msg) => appendActivityRef.current(`[Bridge Error] ${msg}`),
    });
    bridgeRef.current = bridge;
    bridge.connect();
    return () => {
      abortMicRef.current(true);
      bridge.disconnect();
    };
  }, []);

  const sendAction = useCallback(
    (action: string, payload: Record<string, unknown> = {}) => {
      return bridgeRef.current?.sendAction(action, payload) ?? false;
    },
    []
  );

  // Request workspace file list when empty workspace is shown
  useEffect(() => {
    if (workspaceOpen && workspace.mode === "empty") {
      sendAction("list_workspace_files");
    }
  }, [workspaceOpen, workspace.mode, sendAction]);

  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;
    clearStreamSuppression();
    setInputText("");
    sendAction("send_message", { text });
  }, [inputText, clearStreamSuppression, sendAction]);

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

  const handleCodeRun = useCallback(
    (content: string, path: string) => {
      if (!path) return;
      sendAction("code_run", { path, content });
    },
    [sendAction]
  );

  const handleCodeStop = useCallback(() => {
    sendAction("stop");
  }, [sendAction]);

  const handleTextSave = useCallback(
    (content: string, fileName: string) => {
      if (!fileName) return;
      sendAction("save_workspace_file", { path: fileName, content });
    },
    [sendAction]
  );

  const handleAddDocumentPaths = useCallback(() => {
    const input = documentPathInput.trim();
    if (!input) return;
    const paths = input
      .split(/[;\n]+/)
      .map((p) => p.trim())
      .filter((p) => p.length > 0);
    setSelectedDocumentPaths((prev) => [...prev, ...paths]);
    setDocumentPathInput("");
  }, [documentPathInput, setSelectedDocumentPaths]);

  const handleIngestSelected = useCallback(() => {
    if (selectedDocumentPaths.length === 0 || documentIngestActive) return;
    sendAction("document_picker_selected", { paths: selectedDocumentPaths });
  }, [selectedDocumentPaths, documentIngestActive, sendAction]);

  const handleClearDocumentSelection = useCallback(() => {
    setSelectedDocumentPaths([]);
  }, [setSelectedDocumentPaths]);

  const handleStop = useCallback(() => {
    abortMicRecording(true);
    stopStreamingLocally();
    sendAction("stop");
  }, [abortMicRecording, stopStreamingLocally, sendAction]);

  const handleNewSession = useCallback(() => {
    abortMicRecording(true);
    setMessages([]);
    sendAction("new_session");
    resetUI();
    resetRouter();
    workspace.closeFile();
  }, [abortMicRecording, setMessages, sendAction, resetUI, resetRouter, workspace]);

  const handleRestart = useCallback(() => {
    abortMicRecording(true);
    sendAction("restart_piper");
  }, [abortMicRecording, sendAction]);

  const toggleRailPanel = useCallback((panel: RailPanelId) => {
    setExpandedRailPanels((prev) => ({ ...prev, [panel]: !prev[panel] }));
  }, []);

  const handleMicClick = useCallback(() => {
    if (micState === "listening") {
      stopMicRecording();
    } else if (micState === "idle" || micState === "error") {
      clearStreamSuppression();
      startMicRecording();
    }
  }, [micState, stopMicRecording, startMicRecording, clearStreamSuppression]);

  const micDisabled =
    connState !== "connected" ||
    isGenerating ||
    micState === "requesting_permission" ||
    micState === "transcribing";

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
        canStop={isGenerating || ttsState === "synthesizing" || ttsState === "playing"}
        onNewSession={handleNewSession}
        onRestart={handleRestart}
        onStop={handleStop}
        onOpenSystem={() => setSystemDrawerOpen(true)}
      />

      <div className="app-body">
        {/* Column 1: Chat */}
        <div className="chat-col">
          {authWaiting && (
            <div className="auth-banner">
              <span className="auth-icon">🔒</span>
              <span className="auth-text">
                Password required. Type the password below or /cancel.
              </span>
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

        {/* Column 2, Row 1: Center stage */}
        <div className="center-stage">
          <AvatarStage state={avatarState} />
          <ModeSelector styleLabel={styleLabel} userName={userName} />
        </div>

        {/* Column 3: Right rail */}
        <RightRail
          expandedPanels={expandedRailPanels}
          onTogglePanel={toggleRailPanel}
          workspaceOpen={workspaceOpen}
          onToggleWorkspace={() => setWorkspaceOpen(!workspaceOpen)}
          connState={connState}
          sendAction={sendAction}
          documentIngestActive={documentIngestActive}
          documentsView={documentsView}
          documentsViewRef={documentsViewRef}
          selectedDocumentPaths={selectedDocumentPaths}
          documentPathInput={documentPathInput}
          onDocumentPathChange={setDocumentPathInput}
          onAddDocumentPaths={handleAddDocumentPaths}
          onIngestSelected={handleIngestSelected}
          onClearDocumentSelection={handleClearDocumentSelection}
          onCancelIngest={() => sendAction("document_picker_cancel")}
          activities={activities}
          logs={logs}
          rawEvents={filteredRawEvents}
          rawEventFilter={rawEventFilter}
          onRawEventFilterChange={setRawEventFilter}
          liveScreen={liveScreen}
          stats={stats}
        />

        {/* Voice strip */}
        <div className="voice-strip-col">
          <VoiceStrip
            micState={micState}
            micButtonLabel={micButtonLabel}
            micButtonClass={micButtonClass}
            micDisabled={micDisabled}
            micStatusText={micStatusText}
            backendMicStatus={micStatus}
            onMicClick={handleMicClick}
            connState={connState}
            isGenerating={isGenerating}
            isSpeaking={isSpeaking}
          />
        </div>

        {/* Workspace overlay */}
        {workspaceOpen && (
          <WorkspaceOverlay
            workspace={workspace}
            codePreview={codePreview}
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
            onTextContentChange={workspace.setTextContent}
            onTextSave={handleTextSave}
            imageUrl={workspace.imageUrl}
            visionText={workspace.visionText}
            workspaceFiles={workspace.workspaceFiles}
            workspacePath={workspace.workspacePath}
            onFileFromList={(path: string) => {
              const fileName = path.split(/[\\/]/).pop() || path;
              const name = fileName.toLowerCase();
              if (name.endsWith(".py")) {
                workspace.openFile(path, "code");
                setCodePathInput(path);
                sendAction("read_workspace_file", { path });
              } else if (name.endsWith(".txt") || name.endsWith(".md")) {
                workspace.openFile(path, "text");
                sendAction("read_workspace_file", { path });
              } else if (/\.(jpg|jpeg|png|webp)$/.test(name)) {
                workspace.openFile(path, "vision");
                workspace.setVisionImage(workspaceImageUrl(path, workspace.workspacePath));
              }
            }}
            onFileSelected={() => {}}
            onCloseWorkspace={() => setWorkspaceOpen(false)}
            onCloseFile={() => workspace.closeFile()}
            sendAction={sendAction}
            setCodePathInput={setCodePathInput}
          />
        )}
      </div>

      <StatusFooter statsText="" />

      <SystemDrawer
        isOpen={systemDrawerOpen}
        onClose={() => setSystemDrawerOpen(false)}
        connState={connState}
        ttsState={ttsState}
        errors={errors}
        logs={logs}
        userName={userName}
        backendVersion="Piper v2.0"
      />
    </div>
  );
}
