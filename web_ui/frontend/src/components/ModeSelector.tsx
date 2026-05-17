interface ModeSelectorProps {
  styleLabel: string;
}

export default function ModeSelector({ styleLabel }: ModeSelectorProps) {
  const label = styleLabel || "Default";
  return (
    <div className="mode-selector">
      <div className="mode-readout">
        <span className="mode-readout-label">Active Style</span>
        <span className="mode-readout-value">{label}</span>
      </div>
      <span className="mode-readout-hint">Use /style name to switch</span>
    </div>
  );
}
