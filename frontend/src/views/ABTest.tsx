import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, Row } from "../api";
import { useApi } from "../hooks";
import { Colors } from "../theme";
import { num, pct, pValue } from "../format";
import { ChartCard } from "../components/ChartCard";
import { DataTable } from "../components/DataTable";
import { Legend } from "../components/Legend";
import { StatTile } from "../components/StatTile";
import { TimeLineChart } from "../components/TimeLineChart";

const PLAIN: Record<string, string> = {
  paired_t_rolling_sharpe: "Paired t-test on rolling 3-year Sharpe (as specified)",
  hac_t_rolling_sharpe: "Same test, corrected for overlapping windows (Newey-West)",
  paired_t_daily_returns: "Paired t-test on daily returns",
  bootstrap_sharpe_diff: "Block bootstrap, Sharpe difference (1,000 resamples)",
  bootstrap_return_diff: "Block bootstrap, return difference (1,000 resamples)",
  rolling_win_rate: "Share of 3-year windows where 60/40 had the higher Sharpe",
  tracking_error: "Tracking error between the strategies",
};

export function ABTest({ colors }: { colors: Colors }) {
  const ab = useApi(api.ab);
  const score = useApi(() => api.scorecard("MAX"));
  const t = (name: string) => ab.data?.tests.find((x) => x.test_name === name);
  const boot = t("bootstrap_sharpe_diff"), hac = t("hac_t_rolling_sharpe"), naive = t("paired_t_rolling_sharpe");
  const winRate = t("rolling_win_rate"), te = t("tracking_error");
  const port = (code: string) => score.data?.portfolios.find((p) => p.portfolio_code === code);
  const a = port("SPY_60_QQQ_40"), b = port("SPY_50_QQQ_50");
  const significant = boot?.significant || hac?.significant;

  const rolling: Row[] = (ab.data?.rolling ?? []).map((r) => ({ ...r, date: r.window_end }));
  const hist = useMemo(() => histogram((ab.data?.bootstrap ?? []).map((x) => x.sharpe_diff), 36), [ab.data]);

  return (
    <div className="stack">
      <div>
        <h2 className="section-title">A/B test: 60/40 vs 50/50</h2>
        <p className="section-sub">
          Strategy A holds 60% SPY / 40% QQQ; Strategy B holds 50/50. Both rebalance monthly. Null hypothesis: they deliver the same
          risk-adjusted return. Tested over rolling 3-year windows since 1999 and with a 1,000-iteration block bootstrap.
        </p>
      </div>

      <section className={`card ${ab.loading ? "loading" : ""}`}>
        <div className="grid grid-2" style={{ alignItems: "center" }}>
          <div className="stack" style={{ gap: 8 }}>
            <span className="badge">{significant ? "✓ Significant at 5%" : "○ Not significant at 5%"}</span>
            <div className="hero-value" style={{ fontSize: 34 }}>
              {significant ? "One strategy is reliably better" : "No reliable difference"}
            </div>
            <p className="callout" style={{ margin: 0 }}>
              {boot && <>The bootstrap puts the Sharpe difference (A − B) at {num(boot.estimate, 3)}, with a 95% interval of {num(boot.ci_low, 3)} to {num(boot.ci_high, 3)} — the interval {boot.ci_low < 0 && boot.ci_high > 0 ? "includes zero" : "excludes zero"}. </>}
              {naive && hac && <>The naive t-test says p {pValue(naive.p_value)}, but rolling windows overlap almost entirely; corrected for that, p = {pValue(hac.p_value)}.</>}
            </p>
          </div>
          <div className="grid grid-2">
            <StatTile label="Sharpe, 60/40 (A)" value={num(a?.sharpe)} note={`${pct(a?.cagr)} / yr, max DD ${pct(a?.max_drawdown, 0)}`} />
            <StatTile label="Sharpe, 50/50 (B)" value={num(b?.sharpe)} note={`${pct(b?.cagr)} / yr, max DD ${pct(b?.max_drawdown, 0)}`} />
            <StatTile label="Windows where A wins" value={pct(winRate?.estimate, 0)} note={`${winRate?.n_obs ?? "—"} rolling 3-yr windows`} />
            <StatTile label="Tracking error" value={pct(te?.estimate, 2)} note="How far apart the two drift each year" />
          </div>
        </div>
      </section>

      <div className="grid grid-2">
        <ChartCard title="Rolling 3-year Sharpe ratio" subtitle={`Each point is the Sharpe over the 3 years ending that month. The lines nearly overlap: the strategies drift apart by only ${pct(te?.estimate, 2)} a year`}
                   legend={<Legend items={[{ label: "A · 60/40", color: colors["strat-a"] }, { label: "B · 50/50", color: colors["strat-b"] }]} />}
                   loading={ab.loading}
                   table={{ rows: rolling, columns: [
                     { key: "window_end", label: "Window end" }, { key: "sharpe_a", label: "Sharpe A", format: (v) => num(v) },
                     { key: "sharpe_b", label: "Sharpe B", format: (v) => num(v) }, { key: "return_a", label: "Return A", format: (v) => pct(v) },
                     { key: "return_b", label: "Return B", format: (v) => pct(v) }, { key: "tracking_error", label: "TE", format: (v) => pct(v, 2) }] }}>
          <TimeLineChart data={rolling} colors={colors} yFormat={(v) => v.toFixed(1)} referenceY={0}
                         series={[{ key: "sharpe_a", label: "A · 60/40", color: colors["strat-a"] },
                                  { key: "sharpe_b", label: "B · 50/50", color: colors["strat-b"] }]} />
        </ChartCard>

        <ChartCard title="Bootstrap distribution of the Sharpe difference"
                   subtitle="1,000 resampled histories (A − B). Grey bars sit outside the 95% interval"
                   loading={ab.loading}
                   table={{ rows: hist, columns: [
                     { key: "lo", label: "From", format: (v) => num(v, 3) }, { key: "hi", label: "To", format: (v) => num(v, 3) }, { key: "count", label: "Resamples" }] }}>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={hist} barCategoryGap={2} margin={{ top: 20, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid vertical={false} stroke={colors.grid} />
              <XAxis dataKey="mid" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => v.toFixed(2)}
                     tick={{ fill: colors.muted, fontSize: 12 }} tickLine={false} axisLine={{ stroke: colors.axis }} />
              <YAxis tick={{ fill: colors.muted, fontSize: 12 }} tickLine={false} axisLine={false} width={40} />
              <ReferenceLine x={0} stroke={colors.ink} label={{ value: "No difference", position: "top", fill: colors["ink-2"], fontSize: 12 }} />
              <Tooltip cursor={{ fill: colors["surface-2"] }}
                       content={(p: any) => p.active && p.payload?.[0] ? (
                         <div className="tt"><div className="tt-row"><strong>{p.payload[0].payload.count} resamples</strong></div>
                           <div className="tt-title">{num(p.payload[0].payload.lo, 3)} to {num(p.payload[0].payload.hi, 3)}</div></div>) : null} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false}>
                {hist.map((h, i) => (
                  <Cell key={i} fill={boot && (h.hi < boot.ci_low || h.lo > boot.ci_high) ? colors.muted : colors.accent} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <ChartCard title="Sharpe gap between the strategies (A − B)"
                 subtitle="Above zero: 60/40 was better over that 3-year window; below zero: 50/50 was better"
                 loading={ab.loading}
                 table={{ rows: rolling, columns: [
                   { key: "window_end", label: "Window end" },
                   { key: "sharpe_a", label: "Gap (A − B)", format: (_, r) => num(r.sharpe_a - r.sharpe_b, 3) }] }}>
        <TimeLineChart data={rolling.map((r) => ({ date: r.date, gap: r.sharpe_a - r.sharpe_b }))} colors={colors}
                       yFormat={(v) => v.toFixed(2)} referenceY={0} height={220}
                       series={[{ key: "gap", label: "Sharpe gap (A − B)", color: colors.accent }]} />
      </ChartCard>

      <section className="card">
        <div className="card-head">
          <div>
            <h3 className="card-title">All test results</h3>
            <p className="card-sub">Estimates are A minus B. Significance at the 5% level.</p>
          </div>
        </div>
        <DataTable
          rows={ab.data?.tests ?? []}
          columns={[
            { key: "test_name", label: "Test", format: (v) => PLAIN[v] ?? v },
            { key: "estimate", label: "Estimate", format: (v, r) => (r.test_name.includes("return") || r.test_name === "tracking_error" || r.test_name === "rolling_win_rate" ? pct(v, 2) : num(v, 4)) },
            { key: "p_value", label: "p-value", format: (v) => pValue(v) },
            { key: "ci_low", label: "95% CI", format: (v, r) => (v == null ? "—" : r.test_name.includes("return") ? `${pct(v, 2)} to ${pct(r.ci_high, 2)}` : `${num(v, 3)} to ${num(r.ci_high, 3)}`) },
            { key: "significant", label: "Verdict", format: (v) => (v == null ? "descriptive" : v ? "significant" : "not significant") },
          ]}
        />
      </section>
    </div>
  );
}

function histogram(values: number[], bins: number): Row[] {
  if (!values.length) return [];
  const lo = Math.min(...values), hi = Math.max(...values), w = (hi - lo) / bins || 1;
  const out = Array.from({ length: bins }, (_, i) => ({ lo: lo + i * w, hi: lo + (i + 1) * w, mid: lo + (i + 0.5) * w, count: 0 }));
  for (const v of values) out[Math.min(bins - 1, Math.floor((v - lo) / w))].count++;
  return out;
}
