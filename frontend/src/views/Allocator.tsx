import { useEffect, useState } from "react";
import { api, Row } from "../api";
import { useApi } from "../hooks";
import { Colors } from "../theme";
import { money, num, pct } from "../format";
import { AllocationBar } from "../components/AllocationBar";
import { ChartCard } from "../components/ChartCard";
import { StatTile } from "../components/StatTile";
import { FanChart, fanLegend } from "./RiskMonteCarlo";

/** Debounce a value so sliders don't fire a request per pixel. */
function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

const SCENARIOS = [
  { value: "base", label: "Full history (base)" },
  { value: "15y", label: "Last 15 years" },
  { value: "10y", label: "Last 10 years" },
  { value: "5y", label: "Last 5 years" },
  { value: "equal_sharpe", label: "Equal-Sharpe prior" },
  { value: "ml_vol", label: "Current vol regime (ML)" },
];

export function Allocator({ colors }: { colors: Colors }) {
  const [tolerance, setTolerance] = useState(20);
  const tol = useDebounced(tolerance);
  const rec = useApi(() => api.recommend(tol), [tol]);

  const [qqq, setQqq] = useState(50);
  const [equity, setEquity] = useState(100);
  const [years, setYears] = useState(5);
  const [scenario, setScenario] = useState("base");
  const sim = useDebounced({ qqq, equity, years, scenario });
  const what = useApi(
    () => api.simulate({ qqq_weight: sim.qqq / 100, equity_fraction: sim.equity / 100, years: sim.years, scenario: sim.scenario }),
    [sim.qqq, sim.equity, sim.years, sim.scenario],
  );
  const r: Row | null = rec.data;
  const s = what.data?.summary;

  const applyRecommendation = () => {
    if (!r) return;
    setQqq(Math.round(r.recommended_qqq_weight * 100));
    setEquity(Math.round(r.equity_fraction * 100));
    setYears(5);
    setScenario("base");
  };

  return (
    <div className="stack">
      <div>
        <h2 className="section-title">Allocator</h2>
        <p className="section-sub">
          Tell us the biggest one-year loss you could live with in a bad year (a 1-in-20 event). We find the SPY/QQQ mix with the
          best Sharpe ratio inside that limit, adding T-bills only if no all-stock mix fits.
        </p>
      </div>

      <div className="filters">
        <label className="filter-group">
          <span className="filter-label">Max bad-year loss</span>
          <input type="range" min={5} max={40} step={1} value={tolerance} onChange={(e) => setTolerance(Number(e.target.value))} />
          <b>{tolerance}% · {money(tolerance * 500)}</b>
        </label>
      </div>

      <section className={`card ${rec.loading ? "loading" : ""}`}>
        {rec.error && <p className="down">{rec.error}</p>}
        {r && (
          <div className="grid grid-2" style={{ alignItems: "center" }}>
            <div className="stack" style={{ gap: 12 }}>
              <span className="badge">{r.within_tolerance ? "Within your limit" : "Over your limit"}</span>
              <AllocationBar qqq={r.qqq_weight} spy={r.spy_weight} tbill={r.tbill_weight} colors={colors} />
              <p className="callout" style={{ margin: 0 }}>{r.rationale}</p>
              <button className="link-btn" style={{ alignSelf: "flex-start" }} onClick={applyRecommendation}>Load this plan into the simulator ↓</button>
            </div>
            <div className="grid grid-2">
              <StatTile label="Median after 5 years" value={money(r.terminal_p50)} note={`Bad case ${money(r.terminal_p05)}`} />
              <StatTile label="Bad-year loss (VaR)" value={money(r.var95_1y)} note={`${num(r.var95_1y_pct, 1)}% of capital`} />
              <StatTile label="Worst drawdown, 95th pct." value={pct(r.mdd_p95, 0)} />
              <StatTile label="Median Sharpe" value={num(r.sharpe_median)} note={`Expected ${pct(r.exp_return_annual)} / yr`} />
            </div>
          </div>
        )}
      </section>

      <h3 className="section-title" style={{ fontSize: 17 }}>What-if simulator</h3>
      <div className="filters">
        <label className="filter-group">
          <span className="filter-label">QQQ share of stocks</span>
          <input type="range" min={0} max={100} step={5} value={qqq} onChange={(e) => setQqq(Number(e.target.value))} />
          <b style={{ minWidth: 40 }}>{qqq}%</b>
        </label>
        <label className="filter-group">
          <span className="filter-label">Invested in stocks</span>
          <input type="range" min={0} max={100} step={5} value={equity} onChange={(e) => setEquity(Number(e.target.value))} />
          <b style={{ minWidth: 40 }}>{equity}%</b>
        </label>
        <label className="filter-group">
          <span className="filter-label">Horizon</span>
          <select value={years} onChange={(e) => setYears(Number(e.target.value))}>
            {[1, 3, 5, 10, 20].map((y) => <option key={y} value={y}>{y} years</option>)}
          </select>
        </label>
        <label className="filter-group">
          <span className="filter-label">Assumptions</span>
          <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {SCENARIOS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>

      <div className="grid grid-3">
        <ChartCard className="span-2" title={`$50,000 over ${sim.years} years`}
                   subtitle={`${sim.qqq}% QQQ / ${100 - sim.qqq}% SPY on ${sim.equity}% of the money · 5,000 fresh simulated paths`}
                   legend={fanLegend(colors)} loading={what.loading}
                   table={{ rows: what.data?.fan ?? [], columns: [
                     { key: "step", label: "Year", format: (v) => (v / 252).toFixed(1) },
                     { key: "p05", label: "5th", format: money }, { key: "p50", label: "Median", format: money }, { key: "p95", label: "95th", format: money }] }}>
          <FanChart data={what.data?.fan ?? []} colors={colors} />
        </ChartCard>
        <section className={`card stack ${what.loading ? "loading" : ""}`} style={{ gap: 14 }}>
          <StatTile label="Median outcome" value={money(s?.terminal_p50)} note={`CAGR ${pct(s?.cagr_median)}`} />
          <StatTile label="Bad case (5th pct.)" value={money(s?.terminal_p05)} note={`Chance of losing money ${pct(s?.prob_loss_horizon, 1)}`} />
          <StatTile label="Bad-year loss (1-yr VaR)" value={money(s?.var95_1y)} note={`${num(s?.var95_1y_pct, 1)}% of capital`} />
          <StatTile label="Worst drawdown, 95th pct." value={pct(s?.mdd_p95, 0)} note={`Median Sharpe ${num(s?.sharpe_median)}`} />
        </section>
      </div>
    </div>
  );
}
