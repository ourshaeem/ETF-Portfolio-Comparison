export interface LegendItem {
  label: string;
  color: string;
  shape?: "line" | "box";
}

/** Legend mirrors the mark: a line key for lines, a box for bars/areas. */
export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <div className="legend" role="list">
      {items.map((it) => (
        <span key={it.label} role="listitem">
          <i className={it.shape === "box" ? "swatch" : "line-key"} style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}
