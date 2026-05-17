import type { MicState } from "../types";

interface VoiceStripProps {
  micState: MicState;
  micButtonLabel: string;
  micButtonClass: string;
  micDisabled: boolean;
  micStatusText: string;
  onMicClick: () => void;
  connState: string;
}

export default function VoiceStrip({
  micState,
  micButtonLabel,
  micButtonClass,
  micDisabled,
  micStatusText,
  onMicClick,
  connState,
}: VoiceStripProps) {
  return (
    <div className="voice-strip">
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
          {micState === "listening" && (
            <div className="waveform-bars">
              {[...Array(16)].map((_, i) => (
                <div
                  key={i}
                  className="waveform-bar"
                  style={{ animationDelay: `${i * 0.06}s` }}
                />
              ))}
            </div>
          )}
          {micState === "transcribing" && (
            <div className="waveform-bars static">
              {[...Array(16)].map((_, i) => (
                <div
                  key={i}
                  className="waveform-bar"
                  style={{ height: `${4 + Math.sin(i * 0.8) * 3}px` }}
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
        {micStatusText && (
          <div className="mic-status-display">{micStatusText}</div>
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
