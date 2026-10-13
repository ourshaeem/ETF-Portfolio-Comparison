import { money, pct } from "../format";
import { Colors } from "../theme";

/** Part-to-whole of the $50k: segments separated by a 2px surface gap, labelled in a
 *  legend underneath (never colour alone). */
export function AllocationBar({
  qqq, spy, tbill, initial = 50_000, colors,
}: { qqq: number; spy: number; tbill: number; initial?: number; colors: Colors }) {
  const parts = [
    { label: "QQQ", w: qqq, color: colors.qqq },
    { label: "SPY", w: spy, color: colors.spy },
    { label: "T-bills", w: tbill, color: colors.cash },
  ].filter((p) => p.w > 0.0005);
  return (
    <div>
      <div className="alloc-bar" role="img" aria-label={parts.map((p) => `${p.label} ${pct(p.w, 0)}`).join(", ")}>
        {parts.map((p) => (
          <div key={p.label} title={`${p.label}: ${pct(p.w, 1)} (${money(p.w * initial)})`} style={{ flex: p.w, background: p.color }} />
        ))}
      </div>
      <div className="alloc-legend">
        {parts.map((p) => (
          <span key={p.label}>
            <i className="swatch" style={{ background: p.color }} />
            {p.label} <b>{pct(p.w, 0)}</b> <span className="muted">{money(p.w * initial)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
