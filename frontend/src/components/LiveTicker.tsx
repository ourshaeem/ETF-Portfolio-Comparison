import { LiveState } from "../hooks";
import { signedPct } from "../format";

export function LiveTicker({ live }: { live: LiveState }) {
  const streaming = live.source === "yahoo-websocket";
  return (
    <div className="ticker" aria-live="polite">
      <span className="tick" title={live.connected ? `Source: ${live.source}` : "Reconnecting…"}>
        <i className={`live-dot ${live.connected && streaming ? "pulse" : ""}`}
           style={!live.connected ? { background: "var(--muted)" } : undefined} />
        <span className="muted">{live.connected ? (streaming ? "Live" : "Delayed") : "Offline"}</span>
      </span>
      {live.quotes.map((q) => {
        const up = (q.change_pct ?? 0) >= 0;
        return (
          <span className="tick" key={q.symbol}>
            <b>{q.symbol}</b>
            <span>{q.price?.toFixed(2)}</span>
            <span className={up ? "up" : "down"}>
              {up ? "▲" : "▼"} {signedPct(q.change_pct)}
            </span>
          </span>
        );
      })}
    </div>
  );
}
