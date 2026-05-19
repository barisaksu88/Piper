interface ModeSelectorProps {
  styleLabel: string;
  userName?: string;
}

export default function ModeSelector({ styleLabel, userName }: ModeSelectorProps) {
  const label = styleLabel || "Default";
  return (
    <div className="mode-selector">
      <div className="mode-readout">
        {userName && (
          <>
            <span className="mode-readout-label">User</span>
            <span className="mode-readout-value" style={{ fontSize: "13px", marginBottom: "4px" }}>{userName}</span>
          </>
        )}
        <span className="mode-readout-label">Active Style</span>
        <span className="mode-readout-value">{label}</span>
      </div>
      <span className="mode-readout-hint">Use /style name to switch</span>
    </div>
  );
}
