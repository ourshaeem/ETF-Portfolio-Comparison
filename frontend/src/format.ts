const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export const money = (v?: number | null) => (v == null ? "—" : usd0.format(v));

/** Compact currency for axes and tiles: $50K, $1.2M. */
export const moneyCompact = (v?: number | null) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  const trim = (x: string) => x.replace(/\.0$/, "");
  if (a >= 1e6) return `$${trim((v / 1e6).toFixed(a >= 1e7 ? 0 : 1))}M`;
  if (a >= 1e3) return `$${trim((v / 1e3).toFixed(a >= 1e5 ? 0 : 1))}K`;
  return `$${v.toFixed(0)}`;
};

export const pct = (v?: number | null, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const signedPct = (v?: number | null, digits = 2) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(digits)}%`;
export const num = (v?: number | null, digits = 2) => (v == null ? "—" : v.toFixed(digits));
export const pValue = (p?: number | null) => (p == null ? "—" : p < 0.0001 ? "< 0.0001" : p.toFixed(4));
export const year = (d: string) => d.slice(0, 4);
export const shortDate = (d: string) =>
  new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" });
