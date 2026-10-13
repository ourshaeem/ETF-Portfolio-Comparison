import { useMemo, useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, LineChart, ReferenceDot, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, Row } from "../api";
import { useApi } from "../hooks";
import { Colors, SYMBOL_TOKEN } from "../theme";
import { money, moneyCompact, num, pct } from "../format";
import { ChartCard } from "../components/ChartCard";
import { ChartTooltip } from "../components/ChartTooltip";
import { Legend } from "../components/Legend";
import { Segmented } from "../components/Segmented";
import { StatTile } from "../components/StatTile";
import { TimeLineChart } from "../components/TimeLineChart";

const axis = (c: Colors) => ({ tick: { fill: c.muted, fontSize: 12 }, tickLine: false });

export function FanChart({ data, colors, initial = 50_000, height = 320 }: { data: Row[]; colors: Colors; initial?: number; height?: number }) {
  const rows = data.map((d) => ({ ...d, years: d.step / 252, band90: [d.p05, d.p95], band50: [d.p25, d.p75] }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid vertical={false} stroke={colors.grid} />
        <XAxis dataKey="years" type="number" domain={[0, "dataMax"]} tickFormatter={(v) => `${v.toFixed(0)}y`}
               ticks={[0, 1, 2, 3, 4, 5, 10, 15, 20, 30].filter((t) => t <= (rows.at(-1)?.years ?? 5))}
               axisLine={{ stroke: colors.axis }} {...axis(colors)} />
        <YAxis tickFormatter={moneyCompact} axisLine={false} width={64} {...axis(colors)} />
        <ReferenceLine y={initial} stroke={colors.axis} />
        <Tooltip
          cursor={{ stroke: colors["ink-2"], strokeWidth: 1 }}
          content={(p: any) => (
            <ChartTooltip {...p} labelFormat={(l) => `Year ${Number(l).toFixed(1)}`}
                          valueFormat={(v) => moneyCompact(v)} />
          )}
        />
        <Area dataKey="band90" name="5th–95th pct" stroke="none" fill={colors.accent} fillOpacity={0.2} isAnimationActive={false} />
        <Area dataKey="band50" name="25th–75th pct" stroke="none" fill={colors.accent} fillOpacity={0.4} isAnimationActive={false} />
        <Line dataKey="p50" name="Median" stroke={colors.accent} strokeWidth={2} dot={false} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function fanLegend(colors: Colors) {
  return (
    <Legend items={[
      { label: "Median path", color: colors.accent },
      { label: "Middle 50% of outcomes", color: colors.accent + "66", shape: "box" },
      { label: "90% of outcomes", color: colors.accent + "33", shape: "box" },
    ]} />
  );
}

export function RiskMonteCarlo({ colors }: { colors: Colors }) {
  const [w, setW] = useState(0.6);
  const [mlSymbol, setMlSymbol] = useState<"SPY" | "QQQ">("QQQ");
  const frontier = useApi(api.frontier);
  const fan = useApi(() => api.fan(w), [w]);
  const term = useApi(() => api.terminal(w), [w]);
  const scen = useApi(api.scenarios);
  const ml = useApi(api.mlVol);

  const row = frontier.data?.find((r) => Math.abs(r.qqq_weight - w) < 1e-6);
  const maxSharpe = frontier.data?.find((r) => r.is_max_sharpe);
  const front = frontier.data?.map((r) => ({ ...r, pctQ: Math.round(r.qqq_weight * 100) })) ?? [];
  const hist = term.data?.bins.map((b) => ({ mid: (b.lo + b.hi) / 2, count: b.count, loss: b.hi <= 50_000, lo: b.lo, hi: b.hi })) ?? [];

  const mlRows = useMemo(() => {
    const m = new Map<string, Row>();
    for (const p of ml.data?.predictions ?? []) {
      if (p.symbol !== mlSymbol) continue;
      const r = m.get(p.trade_date) ?? { date: p.trade_date, realized: p.realized_vol };
      r[p.model_name] = p.predicted_vol;
      m.set(p.trade_date, r);
    }
    return [...m.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
  }, [ml.data, mlSymbol]);

  const regretMin = scen.data?.regret.find((r) => r.is_minimax_regret);

  return (
    <div className="stack">
      <div>
        <h2 className="section-title">Risk profile & Monte Carlo</h2>
        <p className="section-sub">
          10,000 simulated five-year paths for each SPY/QQQ mix (correlated geometric Brownian motion, calibrated on daily returns since 1999).
          Slide the QQQ share to see how the spread of outcomes, the bad-year loss and the worst drawdown change.
        </p>
      </div>

      <div className="filters">
        <label className="filter-group">
          <span className="filter-label">QQQ share</span>
          <input type="range" min={0} max={100} step={5} value={Math.round(w * 100)} onChange={(e) => setW(Number(e.target.value) / 100)} />
          <b style={{ minWidth: 150 }}>{pct(w, 0)} QQQ / {pct(1 - w, 0)} SPY</b>
        </label>
        {maxSharpe && <button className="link-btn" onClick={() => setW(maxSharpe.qqq_weight)}>Jump to max-Sharpe mix ({pct(maxSharpe.qqq_weight, 0)} QQQ)</button>}
      </div>

      {row && (
        <section className="card grid grid-5">
          <StatTile label="Median after 5 years" value={money(row.terminal_p50)} note={`from ${money(row.initial_investment)}`} />
          <StatTile label="Bad case (5th pct.)" value={money(row.terminal_p05)} note={`Good case ${money(row.terminal_p95)}`} />
          <StatTile label="1-year 95% VaR" value={money(row.var95_1y)} note={`${num(row.var95_1y_pct, 1)}% of capital`} />
          <StatTile label="Worst drawdown, 95th pct." value={pct(row.mdd_p95, 0)} note={`Median ${pct(row.mdd_median, 0)}`} />
          <StatTile label="Chance of a 5-yr loss" value={pct(row.prob_loss_horizon, 1)} note={`Median Sharpe ${num(row.sharpe_median)}`} />
        </section>
      )}

      <div className="grid grid-2">
        <ChartCard title="Range of outcomes for $50,000" subtitle={`${pct(w, 0)} QQQ · bands hold 50% and 90% of the 10,000 simulated paths`}
                   legend={fanLegend(colors)} loading={fan.loading}
                   table={{ rows: fan.data ?? [], columns: [
                     { key: "step", label: "Year", format: (v) => (v / 252).toFixed(1) },
                     { key: "p05", label: "5th", format: money }, { key: "p25", label: "25th", format: money },
                     { key: "p50", label: "Median", format: money }, { key: "p75", label: "75th", format: money },
                     { key: "p95", label: "95th", format: money }] }}>
          <FanChart data={fan.data ?? []} colors={colors} />
        </ChartCard>
        <ChartCard title="Where the 10,000 paths end up" subtitle="Bars left of $50,000 are paths that lose money over 5 years"
                   loading={term.loading}
                   table={{ rows: hist, columns: [
                     { key: "lo", label: "From", format: money }, { key: "hi", label: "To", format: money },
                     { key: "count", label: "Paths" }] }}>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={hist} barCategoryGap={2} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid vertical={false} stroke={colors.grid} />
              <XAxis dataKey="mid" type="number" domain={["dataMin", "dataMax"]} tickFormatter={moneyCompact}
                     axisLine={{ stroke: colors.axis }} {...axis(colors)} />
              <YAxis axisLine={false} width={48} {...axis(colors)} />
              <ReferenceLine x={50_000} stroke={colors["ink-2"]} label={{ value: "$50K start", fill: colors["ink-2"], fontSize: 12, position: "insideTopLeft" }} />
              <Tooltip cursor={{ fill: colors["surface-2"] }}
                       content={(p: any) => p.active && p.payload?.[0] ? (
                         <div className="tt"><div className="tt-row"><strong>{p.payload[0].payload.count} paths</strong></div>
                           <div className="tt-title">{money(p.payload[0].payload.lo)} – {money(p.payload[0].payload.hi)}</div></div>) : null} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false}>
                {hist.map((h, i) => <Cell key={i} fill={h.loss ? colors.muted : colors.accent} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid grid-2">
        <ChartCard title="Risk-adjusted return by QQQ share" subtitle="Median 5-year Sharpe ratio across simulated paths"
                   loading={frontier.loading}
                   table={{ rows: front, columns: [
                     { key: "allocation_label", label: "Mix" }, { key: "sharpe_median", label: "Sharpe", format: (v) => num(v, 3) },
                     { key: "exp_return_annual", label: "Exp. return", format: (v) => pct(v) }, { key: "vol_annual", label: "Volatility", format: (v) => pct(v) }] }}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={front} margin={{ top: 16, right: 16, bottom: 0, left: 4 }}>
              <CartesianGrid vertical={false} stroke={colors.grid} />
              <XAxis dataKey="pctQ" type="number" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v) => `${v}% QQQ`}
                     axisLine={{ stroke: colors.axis }} {...axis(colors)} />
              <YAxis domain={["auto", "auto"]} tickFormatter={(v) => v.toFixed(2)} axisLine={false} width={48} {...axis(colors)} />
              <Tooltip cursor={{ stroke: colors["ink-2"] }} content={(p: any) => <ChartTooltip {...p} labelFormat={(l) => `${l}% QQQ`} valueFormat={(v) => v.toFixed(3)} />} />
              <Line dataKey="sharpe_median" name="Median Sharpe" stroke={colors.accent} strokeWidth={2} isAnimationActive={false}
                    dot={{ r: 3, fill: colors.accent, strokeWidth: 0 }} activeDot={{ r: 5, stroke: colors.surface, strokeWidth: 2 }} />
              {row && <ReferenceDot x={Math.round(w * 100)} y={row.sharpe_median} r={6} fill={colors.ink} stroke={colors.surface} strokeWidth={2} />}
              {maxSharpe && <ReferenceDot x={Math.round(maxSharpe.qqq_weight * 100)} y={maxSharpe.sharpe_median} r={0}
                                          label={{ value: "max", position: "top", fill: colors["ink-2"], fontSize: 12 }} />}
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
        <ChartCard title="Bad-year loss by QQQ share" subtitle="1-year 95% VaR as % of $50,000; lines mark each risk profile's limit"
                   loading={frontier.loading}
                   table={{ rows: front, columns: [
                     { key: "allocation_label", label: "Mix" }, { key: "var95_1y_pct", label: "VaR %", format: (v) => num(v, 1) },
                     { key: "var95_1y", label: "VaR $", format: money }, { key: "mdd_p95", label: "Drawdown 95th", format: (v) => pct(v, 0) }] }}>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={front} margin={{ top: 16, right: 16, bottom: 0, left: 4 }}>
              <CartesianGrid vertical={false} stroke={colors.grid} />
              <XAxis dataKey="pctQ" type="number" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v) => `${v}% QQQ`}
                     axisLine={{ stroke: colors.axis }} {...axis(colors)} />
              <YAxis domain={[0, 35]} ticks={[0, 10, 20, 30]} tickFormatter={(v) => `${v}%`} axisLine={false} width={48} {...axis(colors)} />
              {[10, 20, 30].map((t, i) => (
                <ReferenceLine key={t} y={t} stroke={colors.axis}
                               label={{ value: ["Conservative", "Moderate", "Aggressive"][i], position: "insideTopLeft", fill: colors.muted, fontSize: 11 }} />
              ))}
              <Tooltip cursor={{ stroke: colors["ink-2"] }} content={(p: any) => <ChartTooltip {...p} labelFormat={(l) => `${l}% QQQ`} valueFormat={(v) => `${v.toFixed(1)}%`} />} />
              <Line dataKey="var95_1y_pct" name="1-yr 95% VaR" stroke={colors.accent} strokeWidth={2} isAnimationActive={false}
                    dot={{ r: 3, fill: colors.accent, strokeWidth: 0 }} activeDot={{ r: 5, stroke: colors.surface, strokeWidth: 2 }} />
              {row && <ReferenceDot x={Math.round(w * 100)} y={row.var95_1y_pct} r={6} fill={colors.ink} stroke={colors.surface} strokeWidth={2} />}
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid grid-2">
        <ChartCard title="How sure are we about the best mix?"
                   subtitle="Max-Sharpe QQQ share when the model is calibrated on different histories"
                   loading={scen.loading}
                   table={{ rows: scen.data?.scenarios ?? [], columns: [
                     { key: "scenario", label: "Scenario" }, { key: "max_sharpe_weight", label: "Best QQQ", format: (v) => pct(v, 0) },
                     { key: "spy_mu", label: "SPY drift", format: (v) => pct(v) }, { key: "qqq_mu", label: "QQQ drift", format: (v) => pct(v) },
                     { key: "qqq_sigma", label: "QQQ vol", format: (v) => pct(v) }, { key: "correlation", label: "Corr.", format: (v) => num(v) }] }}>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={scen.data?.scenarios ?? []} layout="vertical" margin={{ top: 4, right: 40, bottom: 0, left: 8 }}>
              <CartesianGrid horizontal={false} stroke={colors.grid} />
              <XAxis type="number" domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickFormatter={(v) => pct(v, 0)} axisLine={{ stroke: colors.axis }} {...axis(colors)} />
              <YAxis type="category" dataKey="scenario" width={170} axisLine={false} {...axis(colors)} />
              <Tooltip cursor={{ fill: colors["surface-2"] }}
                       content={(p: any) => p.active && p.payload?.[0] ? (
                         <div className="tt"><div className="tt-row"><strong>{pct(p.payload[0].payload.max_sharpe_weight, 0)} QQQ</strong></div>
                           <div className="tt-title">{p.payload[0].payload.description}</div></div>) : null} />
              <Bar dataKey="max_sharpe_weight" fill={colors.accent} radius={[0, 4, 4, 0]} maxBarSize={20} isAnimationActive={false}
                   label={{ position: "right", fill: colors["ink-2"], fontSize: 12, formatter: (v: any) => pct(Number(v), 0) }} />
            </BarChart>
          </ResponsiveContainer>
          {regretMin && (
            <p className="callout small" style={{ marginBottom: 0 }}>
              Robust pick: <b>{pct(regretMin.qqq_weight, 0)} QQQ</b> is never more than {num(regretMin.max_regret, 3)} Sharpe below the best mix in any calibration
              (minimax regret). The Sharpe curve is flat near the top, so the real trade-off is extra drawdown risk.
            </p>
          )}
        </ChartCard>

        <ChartCard title="ML volatility forecast vs what happened"
                   subtitle="Next-month volatility: HAR + gradient-boosting hybrid, out-of-sample walk-forward predictions"
                   loading={ml.loading}
                   actions={<Segmented label="ETF" value={mlSymbol} onChange={setMlSymbol} options={[{ value: "SPY", label: "SPY" }, { value: "QQQ", label: "QQQ" }]} />}
                   legend={<Legend items={[{ label: "Realised", color: colors.muted }, { label: "ML forecast (hybrid)", color: colors[SYMBOL_TOKEN[mlSymbol]] }]} />}
                   table={{ rows: (ml.data?.metrics ?? []).filter((m) => m.symbol === mlSymbol), columns: [
                     { key: "model_name", label: "Model" }, { key: "rmse", label: "RMSE", format: (v) => num(v, 4) },
                     { key: "mae", label: "MAE", format: (v) => num(v, 4) }, { key: "r2", label: "R²", format: (v) => num(v, 3) },
                     { key: "qlike", label: "QLIKE", format: (v) => num(v, 4) }] }}>
          <TimeLineChart data={mlRows} colors={colors} yFormat={(v) => pct(v, 0)}
                         series={[{ key: "realized", label: "Realised", color: colors.muted },
                                  { key: "har_gbm_hybrid", label: "ML forecast", color: colors[SYMBOL_TOKEN[mlSymbol]] }]} />
          <MlScore metrics={ml.data?.metrics ?? []} symbol={mlSymbol} />
        </ChartCard>
      </div>
    </div>
  );
}

function MlScore({ metrics, symbol }: { metrics: Row[]; symbol: string }) {
  const rows = metrics.filter((m) => m.symbol === symbol);
  const hybrid = rows.find((m) => m.model_name === "har_gbm_hybrid");
  const naive = rows.find((m) => m.model_name === "naive_rv21");
  if (!hybrid || !naive) return null;
  return (
    <p className="small muted" style={{ margin: "8px 0 0" }}>
      Out-of-sample error {num(hybrid.rmse * 100, 1)} vol points vs {num(naive.rmse * 100, 1)} for "next month = last month"
      ({pct(1 - hybrid.rmse / naive.rmse, 0)} better); R² {num(hybrid.r2, 2)}. Open the table for all four models.
    </p>
  );
}

