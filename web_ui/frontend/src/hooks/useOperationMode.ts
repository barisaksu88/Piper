import { useState, useCallback } from "react";

export type OperationMode = "booting" | "chat";

export interface BootStep {
  name: string;
  status: "pending" | "done" | "error";
}

export function useOperationMode() {
  const [mode, setMode] = useState<OperationMode>("booting");
  const [steps, setSteps] = useState<BootStep[]>([]);
  const [bootMessage, setBootMessage] = useState("Initializing Piper...");

  const handleBootProgress = useCallback((stepName: string, status: BootStep["status"]) => {
    setSteps((prev) => {
      const existing = prev.findIndex((s) => s.name === stepName);
      if (existing >= 0) {
        const next = [...prev];
        next[existing] = { name: stepName, status };
        return next;
      }
      return [...prev, { name: stepName, status }];
    });
    if (status === "error") {
      setBootMessage(`Error: ${stepName}`);
    }
  }, []);

  const handleBootLog = useCallback((text: string) => {
    const line = text.trim();
    if (!line) return;
    const isError = line.toLowerCase().includes("error") || line.toLowerCase().includes("fail");
    handleBootProgress(line, isError ? "error" : "done");
  }, [handleBootProgress]);

  const handleBootReady = useCallback(() => {
    setMode("chat");
    setBootMessage("");
  }, []);

  return {
    mode,
    steps,
    bootMessage,
    handleBootProgress,
    handleBootLog,
    handleBootReady,
    isOperational: mode === "chat",
  };
}
