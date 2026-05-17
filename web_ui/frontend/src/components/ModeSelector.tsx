import { useState } from "react";

const MODES = ["Secretary", "Scientist", "Analyst", "Engineer"];

export default function ModeSelector() {
  const [active, setActive] = useState("Secretary");
  return (
    <div className="mode-selector">
      {MODES.map((mode) => {
        const isActive = active === mode;
        return (
          <button
            key={mode}
            className={`mode-tab ${isActive ? "active" : ""}`}
            onClick={() => setActive(mode)}
            title={`Select ${mode} mode`}
          >
            {mode}
          </button>
        );
      })}
    </div>
  );
}
