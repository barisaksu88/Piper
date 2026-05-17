const MODES = ["Secretary", "Scientist", "Analyst", "Engineer"];

interface ModeSelectorProps {
  activeMode: string;
}

export default function ModeSelector({ activeMode }: ModeSelectorProps) {
  const normalized = activeMode.toLowerCase();
  return (
    <div className="mode-selector">
      {MODES.map((mode) => {
        const isActive = normalized.includes(mode.toLowerCase());
        return (
          <button
            key={mode}
            className={`mode-tab ${isActive ? "active" : ""}`}
            disabled
            title="Visual only — mode switching not yet implemented"
          >
            {mode}
          </button>
        );
      })}
    </div>
  );
}
