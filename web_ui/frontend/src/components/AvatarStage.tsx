interface AvatarStageProps {
  state: "idle" | "listening" | "transcribing" | "thinking" | "speaking";
  modeText: string;
}

const STATE_LABELS: Record<string, string> = {
  idle: "Idle",
  listening: "Listening...",
  transcribing: "Transcribing...",
  thinking: "Thinking...",
  speaking: "Speaking...",
};

export default function AvatarStage({ state, modeText }: AvatarStageProps) {
  return (
    <div className={`avatar-stage ${state}`}>
      <div className="avatar-card">
        <div className="avatar-portrait">
          <div className="avatar-glyph">P</div>
          <div className="avatar-ring" />
        </div>
        <div className="avatar-state-badge">{STATE_LABELS[state] || state}</div>
        {modeText && <div className="avatar-mode-label">{modeText}</div>}
      </div>
    </div>
  );
}
