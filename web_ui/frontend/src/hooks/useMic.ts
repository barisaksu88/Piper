import { useCallback, useEffect, useRef, useState } from "react";
import type { PiperBridge } from "../bridge";
import type { MicStatus } from "../types";
import { blobToBase64, chooseMimeType, formatFromMimeType } from "../utils";

export type MicState = "idle" | "requesting_permission" | "listening" | "transcribing" | "error";

interface UseMicOptions {
  bridgeRef: React.RefObject<PiperBridge | null>;
  appendActivity: (text: string) => void;
}

interface UseMicReturn {
  micState: MicState;
  micError: string;
  micStageMessage: string;
  startMicRecording: () => Promise<void>;
  stopMicRecording: () => void;
  abortMicRecording: (discard?: boolean) => void;
  handleBackendMicStatus: (status: MicStatus) => void;
  // Computed display values
  micButtonLabel: string;
  micButtonClass: string;
  micStatusText: string;
}

export function useMic({ bridgeRef, appendActivity }: UseMicOptions): UseMicReturn {
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

  const experimentalMicUpload = import.meta.env.VITE_PIPER_EXPERIMENTAL_MIC_UPLOAD === "true";

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

  const cleanupMediaRecorder = useCallback(() => {
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
    audioChunksRef.current = [];
  }, []);

  const abortMicRecording = useCallback((discard = true) => {
    if (discard) {
      discardNextMicStopRef.current = true;
    }

    if (experimentalMicUpload) {
      cleanupMediaRecorder();
    }

    // In native mode, tell backend to stop if we're still listening
    if (!experimentalMicUpload && micStateRef.current === "listening") {
      bridgeRef.current?.sendAction("mic_stop", {});
    }

    if (micSubmitTimeoutRef.current) {
      clearTimeout(micSubmitTimeoutRef.current);
      micSubmitTimeoutRef.current = null;
    }

    const shouldReset =
      micStateRef.current === "listening" ||
      micStateRef.current === "requesting_permission" ||
      micStateRef.current === "transcribing";

    if (shouldReset) {
      setMicState("idle");
      setMicError("");
      setMicStageMessage("");
    }
  }, [bridgeRef, experimentalMicUpload, cleanupMediaRecorder]);

  const startMicRecording = useCallback(async () => {
    if (micStateRef.current !== "idle" && micStateRef.current !== "error") return;

    if (!experimentalMicUpload) {
      // Native backend mic mode
      setMicState("listening");
      setMicError("");
      bridgeRef.current?.sendAction("mic_start", {});
      return;
    }

    // Experimental browser upload mode
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
  }, [abortMicRecording, appendActivity, bridgeRef, experimentalMicUpload]);

  const handleBackendMicStatus = useCallback((status: MicStatus) => {
    if (experimentalMicUpload) {
      // In experimental mode, backend acks are only expected during transcribing
      // because the frontend manages the recording directly.
      if (micStateRef.current === "listening") return;
      if (micStateRef.current === "requesting_permission") return;
      if (micStateRef.current === "transcribing") {
        if (status.state === "idle") {
          if (micSubmitTimeoutRef.current) {
            clearTimeout(micSubmitTimeoutRef.current);
            micSubmitTimeoutRef.current = null;
          }
          setMicState("idle");
          setMicError("");
          setMicStageMessage("");
        } else if (status.state === "error") {
          if (micSubmitTimeoutRef.current) {
            clearTimeout(micSubmitTimeoutRef.current);
            micSubmitTimeoutRef.current = null;
          }
          setMicState("error");
          setMicError(status.error || status.message || "Mic error");
        }
      }
      return;
    }

    // Native backend mic mode: backend drives the lifecycle
    if (status.state === "idle") {
      if (micSubmitTimeoutRef.current) {
        clearTimeout(micSubmitTimeoutRef.current);
        micSubmitTimeoutRef.current = null;
      }
      setMicState("idle");
      setMicError("");
      setMicStageMessage("");
    } else if (status.state === "error") {
      if (micSubmitTimeoutRef.current) {
        clearTimeout(micSubmitTimeoutRef.current);
        micSubmitTimeoutRef.current = null;
      }
      setMicState("error");
      setMicError(status.error || status.message || "Mic error");
    } else if (status.state === "listening") {
      setMicState("listening");
      setMicError("");
    } else if (status.state === "transcribing") {
      setMicState("transcribing");
      setMicStageMessage("Transcribing...");
    }
  }, [experimentalMicUpload]);

  const stopMicRecording = useCallback(() => {
    if (micStateRef.current !== "listening") return;

    if (!experimentalMicUpload) {
      // Native backend mic mode
      bridgeRef.current?.sendAction("mic_stop", {});
      setMicState("transcribing");
      return;
    }

    // Experimental browser upload mode
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
  }, [bridgeRef, experimentalMicUpload]);

  // Computed display values
  const micButtonLabel =
    micState === "listening"
      ? "STOP"
      : micState === "requesting_permission" || micState === "transcribing"
        ? "..."
        : "MIC";

  const micButtonClass =
    micState === "listening" ? "danger mic-listening" : micState === "error" ? "mic-error" : "";

  const micStatusText =
    micState === "listening"
      ? "Listening..."
      : micState === "transcribing"
        ? micStageMessage || "Transcribing..."
        : micState === "error"
          ? micError
          : "";

  return {
    micState,
    micError,
    micStageMessage,
    startMicRecording,
    stopMicRecording,
    abortMicRecording,
    handleBackendMicStatus,
    micButtonLabel,
    micButtonClass,
    micStatusText,
  };
}
