import { useState, useCallback } from "react";

export type WorkspaceMode = "empty" | "code" | "text" | "vision" | "project";

export interface WorkspaceState {
  mode: WorkspaceMode;
  filePath: string;
  // Code mode
  codeContent: string;
  codeOutput: string[];
  codeRunning: boolean;
  stdinText: string;
  // Text mode
  textContent: string;
  // Vision mode
  imageUrl: string;
  visionText: string;
}

export function useWorkspace() {
  const [state, setState] = useState<WorkspaceState>({
    mode: "empty",
    filePath: "",
    codeContent: "",
    codeOutput: [],
    codeRunning: false,
    stdinText: "",
    textContent: "",
    imageUrl: "",
    visionText: "",
  });

  const openFile = useCallback((filePath: string, mode: WorkspaceMode) => {
    setState((prev) => ({ ...prev, filePath, mode }));
  }, []);

  const closeFile = useCallback(() => {
    setState((prev) => ({
      ...prev,
      mode: "empty",
      filePath: "",
      codeContent: "",
      codeOutput: [],
      codeRunning: false,
      stdinText: "",
      textContent: "",
      imageUrl: "",
      visionText: "",
    }));
  }, []);

  const setCodeContent = useCallback((content: string) => {
    setState((prev) => ({ ...prev, codeContent: content }));
  }, []);

  const appendCodeOutput = useCallback((line: string) => {
    setState((prev) => ({
      ...prev,
      codeOutput: [...prev.codeOutput, line].slice(-500),
    }));
  }, []);

  const clearCodeOutput = useCallback(() => {
    setState((prev) => ({ ...prev, codeOutput: [] }));
  }, []);

  const setCodeRunning = useCallback((running: boolean) => {
    setState((prev) => ({ ...prev, codeRunning: running }));
  }, []);

  const setStdinText = useCallback((text: string) => {
    setState((prev) => ({ ...prev, stdinText: text }));
  }, []);

  const setTextContent = useCallback((content: string) => {
    setState((prev) => ({ ...prev, textContent: content }));
  }, []);

  const setVisionImage = useCallback((url: string) => {
    setState((prev) => ({ ...prev, imageUrl: url }));
  }, []);

  const setVisionText = useCallback((text: string) => {
    setState((prev) => ({ ...prev, visionText: text }));
  }, []);

  return {
    ...state,
    openFile,
    closeFile,
    setCodeContent,
    appendCodeOutput,
    clearCodeOutput,
    setCodeRunning,
    setStdinText,
    setTextContent,
    setVisionImage,
    setVisionText,
  };
}
