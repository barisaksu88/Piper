import { useState, useCallback } from "react";

export function usePiperUI() {
  // Status
  const [mode, setMode] = useState("");
  const [statusText, setStatusText] = useState("IDLE");
  const [detailModeText, setDetailModeText] = useState("");
  const [stepText, setStepText] = useState("");

  // Identity
  const [userName, setUserName] = useState("User");
  const [styleLabel, setStyleLabel] = useState("Default");
  const [authWaiting, setAuthWaiting] = useState(false);

  // TTS
  const [ttsState, setTtsState] = useState("idle");
  const [ttsError, setTtsError] = useState("");

  // Connection
  const [bootReady, setBootReady] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);

  // Reset all UI state (for new session)
  const resetUI = useCallback(() => {
    setMode("");
    setStatusText("Idle");
    setStepText("");
    setAuthWaiting(false);
  }, []);

  return {
    mode, setMode,
    statusText, setStatusText,
    detailModeText, setDetailModeText,
    stepText, setStepText,
    userName, setUserName,
    styleLabel, setStyleLabel,
    authWaiting, setAuthWaiting,
    ttsState, setTtsState,
    ttsError, setTtsError,
    bootReady, setBootReady,
    workspaceOpen, setWorkspaceOpen,
    resetUI,
  };
}
