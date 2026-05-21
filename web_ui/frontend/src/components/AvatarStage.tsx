interface AvatarStageProps {
  state: "idle" | "listening" | "transcribing" | "thinking" | "generating" | "speaking";
}

const STATE_META: Record<
  string,
  { label: string; color: string; glow: string }
> = {
  idle: {
    label: "Idle",
    color: "#6b7280",
    glow: "rgba(107, 114, 128, 0.2)",
  },
  listening: {
    label: "Listening",
    color: "#3b82f6",
    glow: "rgba(59, 130, 246, 0.35)",
  },
  transcribing: {
    label: "Transcribing",
    color: "#c8a45c",
    glow: "rgba(200, 164, 92, 0.35)",
  },
  thinking: {
    label: "Thinking",
    color: "#3b82f6",
    glow: "rgba(59, 130, 246, 0.35)",
  },
  generating: {
    label: "Generating",
    color: "#22c55e",
    glow: "rgba(34, 197, 94, 0.35)",
  },
  speaking: {
    label: "Speaking",
    color: "#22c55e",
    glow: "rgba(34, 197, 94, 0.35)",
  },
};

export default function AvatarStage({ state }: AvatarStageProps) {
  const meta = STATE_META[state] || STATE_META.idle;
  return (
    <div className={`avatar-stage ${state}`}>
      <div className="avatar-card">
        <div className="avatar-portrait">
          <div
            className="avatar-glyph"
            style={{
              color: meta.color,
              borderColor: meta.color,
              boxShadow: `0 0 60px ${meta.glow}, inset 0 0 40px ${meta.glow}`,
            }}
          >
            <img src="/piper-avatar.png" alt="Piper" className="avatar-img" />
          </div>
          <div className="avatar-ring ring-outer" />
          <div className="avatar-ring ring-mid" />
          <div className="avatar-ring ring-inner" />
        </div>
        <div
          className="avatar-state-badge"
          style={{
            color: meta.color,
            borderColor: meta.color,
            background: `${meta.glow.replace(/\d+\.?\d*\)$/, "0.1)")}`,
          }}
        >
          {meta.label}
        </div>
      </div>
    </div>
  );
}
