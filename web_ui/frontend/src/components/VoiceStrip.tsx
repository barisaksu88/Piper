import type { MicState } from "../types";

interface VoiceStripProps {
  micState: MicState;
  micButtonLabel: string;
  micButtonClass: string;
  micDisabled: boolean;
  micStatusText: string;
  onMicClick: () => void;
  connState: string;
  isGenerating?: boolean;
  isSpeaking?: boolean;
}

export default function VoiceStrip({
  micState,
  micButtonLabel,
  micButtonClass,
  micDisabled,
  micStatusText,
  onMicClick,
  connState,
  isGenerating,
  isSpeaking,
}: VoiceStripProps) {
  const stripState =
    micState === "listening"
      ? "listening"
      : micState === "transcribing"
      ? "transcribing"
      : micState === "error"
      ? "error"
      : isSpeaking
      ? "speaking"
      : isGenerating
      ? "generating"
      : "idle";

  const statusDisplay = micStatusText || (isSpeaking ? "Speaking..." : isGenerating ? "Generating reply..." : "");

  return (
    <div className={`voice-strip ${stripState}`}>
      <div className="voice-left">
        <div className="voice-indicator">
          <span
            className={`voice-dot ${
              micState === "listening"
                ? "pulse"
                : micState === "error"
                ? "error"
                : connState === "connected"
                ? "online"
                : "offline"
            }`}
          />
          <span className="voice-label">Voice</span>
        </div>
        <div className="waveform-placeholder">
          {(micState === "listening" || isSpeaking) && (
            <div className="waveform-bars">
              {[...Array(20)].map((_, i) => (
                <div
                  key={i}
                  className="waveform-bar"
                  style={{ animationDelay: `${i * 0.05}s` }}
                />
              ))}
            </div>
          )}
          {micState === "transcribing" && (
            <div className="waveform-bars static">
              {[...Array(20)].map((_, i) => (
                <div
                  key={i}
                  className="waveform-bar"
                  style={{ height: `${4 + Math.sin(i * 0.7) * 3}px` }}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="voice-center">
        <button
          className={`mic-btn ${micButtonClass}`}
          onClick={onMicClick}
          disabled={micDisabled}
          title={
            micState === "listening"
              ? "Stop listening"
              : "Start microphone"
          }
        >
          <span className="mic-icon">
            {micState === "listening" ? "◼" : "🎤"}
          </span>
          <span className="mic-label">{micButtonLabel}</span>
        </button>
        {statusDisplay && (
          <div className="mic-status-display">{statusDisplay}</div>
        )}
      </div>

      <div className="voice-right">
        <span
          className={`voice-badge ${
            connState === "connected" ? "online" : "offline"
          }`}
        >
          {connState === "connected"
            ? "Local / Online"
            : "Local / Offline"}
        </span>
      </div>
    </div>
  );
}
