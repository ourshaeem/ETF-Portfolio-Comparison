import { useState } from "react";
import { api, Row } from "../api";
import { useApi, LiveState } from "../hooks";
import { Colors } from "../theme";
import { money, num, pct, pValue, signedPct } from "../format";
import { AllocationBar } from "../components/AllocationBar";
import { Segmented } from "../components/Segmented";
import { StatTile } from "../components/StatTile";

const PROFILES = ["Conservative", "Moderate", "Aggressive"] as const;

export function Overview({ colors, live, meta }: { colors: Colors; live: LiveState; meta: Row | null }) {
  const [profile, setProfile] = useState<(typeof PROFILES)[number]>("Moderate");
  const recs = useApi(api.recommendations);
  const ab = useApi(api.ab);
  const sc3 = useApi(() => api.scorecard("3Y"));
  const scMax = useApi(() => api.scorecard("MAX"));
  const scen = useApi(api.scenarios);
  const ml = useApi(api.mlVol);

  const rec = recs.data?.find((r) => r.risk_profile === profile);
  const livePnl = live.portfolios.find((p) => p.risk_profile === profile);
  const findings = buildFindings(sc3.data, scMax.data, ab.data, scen.data, ml.data);
  const mc = meta?.monte_carlo_config;

  return (
    <div className="stack">
      <div>
        <h2 className="section-title">How should $50,000 be split between SPY and QQQ?</h2>
        <p className="section-sub">
          We simulate 10,000 five-year futures for every mix from 0% to 100% QQQ and pick the mix with the best
          risk-adjusted return (Sharpe ratio) that keeps a bad year's loss within your tolerance.
          {mc && <> Calibrated on {mc.calibration_start.slice(0, 4)}–{mc.calibration_end.slice(0, 4)} prices; risk-free rate {pct(mc.risk_free_annual, 2)}.</>}
        </p>
      </div>

      <div className="filters">
        <div className="filter-group">
          <span className="filter-label">Risk profile</span>
          <Segmented label="Risk profile" value={profile} onChange={setProfile}
                     options={PROFILES.map((p) => ({ value: p, label: p }))} />
        </div>
        {rec && <span className="muted small">{rec.description}</span>}
      </div>

      {rec && (
        <section className={`card ${recs.loading ? "loading" : ""}`}>
          <div className="grid grid-2" style={{ alignItems: "center" }}>
            <div className="stack" style={{ gap: 12 }}>
              <StatTile hero label="Median value after 5 years" value={money(rec.terminal_p50)}
                        note={`1-in-20 bad case ${money(rec.terminal_p05)} · 1-in-20 good case ${money(rec.terminal_p95)}`} />
              <AllocationBar qqq={rec.qqq_weight} spy={rec.spy_weight} tbill={rec.tbill_weight} colors={colors} />
              <p className="callout" style={{ margin: 0 }}>{rec.rationale}</p>
            </div>
            <div className="grid grid-2">
              <StatTile label="Bad-year loss (1-yr 95% VaR)" value={money(rec.var95_1y)} note={`${num(rec.var95_1y_pct, 1)}% of $50,000`} />
              <StatTile label="Worst drawdown (95th pct.)" value={pct(rec.mdd_p95, 0)} note="Peak-to-trough fall over 5 yrs" />
              <StatTile label="Median 5-yr Sharpe" value={num(rec.sharpe_median)} note={`Expected return ${pct(rec.exp_return_annual)} / yr`} />
              <StatTile
                label="Today on this plan (live)"
                value={livePnl ? <span className={livePnl.day_pnl >= 0 ? "up" : "down"}>{livePnl.day_pnl >= 0 ? "▲" : "▼"} {money(Math.abs(livePnl.day_pnl))}</span> : "—"}
                note={livePnl ? signedPct(livePnl.day_pnl_pct) + " · streamed" : "Waiting for quotes"}
              />
            </div>
          </div>
        </section>
      )}

      <div className="grid grid-2">
        <section className="card">
          <div className="card-head"><h3 className="card-title">What the data says</h3></div>
          {findings.length ? <ul className="findings">{findings.map((f, i) => <li key={i}>{f}</li>)}</ul> : <p className="muted">Loading…</p>}
        </section>
        <section className="card">
          <div className="card-head">
            <div>
              <h3 className="card-title">Recommended split by risk profile</h3>
              <p className="card-sub">Max-Sharpe mix within each profile's 1-year VaR limit; T-bills fill the gap when no all-equity mix fits.</p>
            </div>
          </div>
          <div className="stack" style={{ gap: 18 }}>
            {recs.data?.map((r) => (
              <div key={r.risk_profile}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <b>{r.risk_profile}</b>
                  <span className="muted small">VaR ≤ {r.max_var95_1y_pct}% · median {money(r.terminal_p50)}</span>
                </div>
                <AllocationBar qqq={r.qqq_weight} spy={r.spy_weight} tbill={r.tbill_weight} colors={colors} />
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function buildFindings(sc3: any, scMax: any, ab: any, scen: any, ml: any): string[] {
  const out: string[] = [];
  const get = (sc: any, s: string) => sc?.assets?.find((a: Row) => a.symbol === s);
  const q3 = get(sc3, "QQQ"), s3 = get(sc3, "SPY");
  if (q3 && s3) {
    const beatsReturn = q3.cagr > s3.cagr, beatsSharpe = q3.sharpe > s3.sharpe;
    out.push(
      `Over the last 3 years QQQ ${beatsReturn ? "beat" : "trailed"} SPY on return (${pct(q3.cagr)} vs ${pct(s3.cagr)} a year) ` +
      `but ${beatsSharpe === beatsReturn ? "also " : ""}${beatsSharpe ? "won" : "lost"} on risk-adjusted return (Sharpe ${num(q3.sharpe)} vs ${num(s3.sharpe)}).`,
    );
  }
  const qm = get(scMax, "QQQ"), sm = get(scMax, "SPY");
  if (qm && sm) {
    out.push(`Since 1999 QQQ returned ${pct(qm.cagr)} a year vs ${pct(sm.cagr)} for SPY, with deeper worst drawdowns (${pct(qm.max_drawdown, 0)} vs ${pct(sm.max_drawdown, 0)}).`);
  }
  const boot = ab?.tests?.find((t: Row) => t.test_name === "bootstrap_sharpe_diff");
  const hac = ab?.tests?.find((t: Row) => t.test_name === "hac_t_rolling_sharpe");
  if (boot && hac) {
    const sig = boot.significant || hac.significant;
    out.push(`60/40 vs 50/50 SPY/QQQ: ${sig ? "a statistically significant" : "no statistically significant"} difference in Sharpe ratio (bootstrap p = ${pValue(boot.p_value)}, overlap-corrected t-test p = ${pValue(hac.p_value)}).`);
  }
  if (scen?.scenarios?.length) {
    const ws = scen.scenarios.filter((s: Row) => s.scenario_code !== "ml_vol").map((s: Row) => s.max_sharpe_weight);
    const mm = scen.regret?.find((r: Row) => r.is_minimax_regret);
    out.push(`The best QQQ share depends on which history you trust: ${pct(Math.min(...ws), 0)}–${pct(Math.max(...ws), 0)} across calibration windows. ${mm ? `${pct(mm.qqq_weight, 0)} QQQ is never more than ${num(mm.max_regret, 3)} Sharpe from the best.` : ""}`);
  }
  const fc = ml?.forecast?.filter((f: Row) => f.model_name === "har_gbm_hybrid");
  const base = scen?.scenarios?.find((s: Row) => s.scenario_code === "base");
  if (fc?.length && base) {
    const longRun = (s: string) => (s === "SPY" ? base.spy_sigma : base.qqq_sigma);
    const calmer = fc.every((f: Row) => f.forecast_vol < longRun(f.symbol));
    const louder = fc.every((f: Row) => f.forecast_vol > longRun(f.symbol));
    out.push(
      `ML forecast of next-month volatility: ${fc.map((f: Row) => `${f.symbol} ${pct(f.forecast_vol, 0)} (long-run ${pct(longRun(f.symbol), 0)})`).join(", ")} — ` +
      `${calmer ? "a calmer market than usual" : louder ? "a more turbulent market than usual" : "mixed versus history"}.`,
    );
  }
  return out;
}
