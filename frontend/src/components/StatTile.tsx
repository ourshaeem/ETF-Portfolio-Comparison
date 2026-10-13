import { ReactNode } from "react";

export function StatTile({ label, value, note, hero }: { label: string; value: ReactNode; note?: ReactNode; hero?: boolean }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={hero ? "hero-value" : "stat-value"}>{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}
