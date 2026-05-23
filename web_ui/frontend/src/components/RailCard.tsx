import type { ReactNode } from "react";

interface RailCardProps {
  title: string;
  children: ReactNode;
  badge?: ReactNode;
  compact?: boolean;
  collapsible?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
}

export default function RailCard({
  title,
  children,
  badge,
  compact = false,
  collapsible = false,
  expanded = true,
  onToggle,
}: RailCardProps) {
  const bodyId = `rail-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const className = [
    "rail-card",
    compact ? "compact" : "",
    collapsible ? "collapsible" : "",
    collapsible && expanded ? "expanded" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={className}>
      <button
        type="button"
        className="rail-card-header"
        onClick={collapsible ? onToggle : undefined}
        aria-expanded={collapsible ? expanded : undefined}
        aria-controls={collapsible ? bodyId : undefined}
        aria-disabled={!collapsible}
        tabIndex={collapsible ? 0 : -1}
      >
        <h3>{title}</h3>
        <span className="rail-card-header-meta">
          {badge}
          {collapsible && (
            <span className="rail-toggle">{expanded ? "Collapse" : "Expand"}</span>
          )}
        </span>
      </button>
      {(!collapsible || expanded) && (
        <div className="rail-card-body" id={bodyId}>
          {children}
        </div>
      )}
    </div>
  );
}
