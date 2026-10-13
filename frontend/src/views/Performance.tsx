import { useMemo, useState } from "react";
import { api, Row } from "../api";
import { useApi } from "../hooks";
import { Colors, SYMBOL_TOKEN } from "../theme";
import { moneyCompact, num, pct } from "../format";
import { ChartCard } from "../components/ChartCard";
import { DataTable } from "../components/DataTable";
import { Legend } from "../components/Legend";
import { Segmented } from "../components/Segmented";
import { TimeLineChart } from "../components/TimeLineChart";

const ALL = ["SPY", "QQQ", "DIA", "IWM", "VTI"];
const PERIODS = [
  { value: "1Y", label: "1Y", years: 1 },
  { value: "3Y", label: "3Y", years: 3 },
  { value: "5Y", label: "5Y", years: 5 },
  { value: "10Y", label: "10Y", years: 10 },
  { value: "MAX", label: "Since 1999", years: 0 },
] as const;
type Period = (typeof PERIODS)[number]["value"];

function startFor(period: Period): string | undefined {
  const p = PERIODS.find((x) => x.value === period)!;
  if (!p.years) return undefined;
  const d = new Date();
  d.setFullYear(d.getFullYear() - p.years);
  return d.toISOString().slice(0, 10);
}

/** Long rows (symbol, trade_date, metric) -> wide rows keyed by date. */
function pivot(rows: Row[] | null, metric: string): Row[] {
  if (!rows) return [];
  const byDate = new Map<string, Row>();
  for (const r of rows) {
    const row = byDate.get(r.trade_date) ?? { date: r.trade_date };
    row[r.symbol] = r[metric];
    byDate.set(r.trade_date, row);
  }
  return [...byDate.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
}

export function Performance({ colors }: { colors: Colors }) {
  const [symbols, setSymbols] = useState<string[]>(["SPY", "QQQ"]);
  const [period, setPeriod] = useState<Period>("10Y");
  const [log, setLog] = useState(false);
  const start = startFor(period);
  const key = symbols.join(",");

  const growth = useApi(() => api.growth(symbols, start), [key, start]);
  const risk = useApi(() => api.risk(symbols, start), [key, start]);
  const score = useApi(() => api.scorecard(period), [period]);

  const series = ALL.filter((s) => symbols.includes(s)).map((s) => ({ key: s, label: s, color: colors[SYMBOL_TOKEN[s]] }));
  const legend = <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />;
  const vol = useMemo(() => pivot(risk.data, "rolling_vol_63d"), [risk.data]);
  const dd = useMemo(() => pivot(risk.data, "drawdown"), [risk.data]);
  const yld = useMemo(() => pivot(risk.data, "ttm_dividend_yield"), [risk.data]);
  const last = growth.data?.[growth.data.length - 1];

  const toggle = (s: string) =>
    setSymbols((cur) => (cur.includes(s) ? (cur.length > 1 ? cur.filter((x) => x !== s) : cur) : [...cur, s]));

  const tableCols = (fmt: (v: number) => string) => [
    { key: "date", label: "Date" },
    ...series.map((s) => ({ key: s.key, label: s.label, format: (v: any) => (v == null ? "—" : fmt(v)) })),
  ];

  return (
    <div className="stack">
      <div>
        <h2 className="section-title">Historical performance</h2>
        <p className="section-sub">Growth of $50,000 with dividends reinvested, rolling risk and income. Toggle ETFs and the time window; everything below follows the same selection.</p>
      </div>

      <div className="filters">
        <div className="filter-group">
          <span className="filter-label">Period</span>
          <Segmented label="Period" value={period} onChange={setPeriod} options={PERIODS.map((p) => ({ value: p.value, label: p.label }))} />
        </div>
        <div className="filter-group" role="group" aria-label="ETFs">
          <span className="filter-label">ETFs</span>
          {ALL.map((s) => (
            <button key={s} className="chip" aria-pressed={symbols.includes(s)} onClick={() => toggle(s)}>
              <i className="swatch" style={{ background: colors[SYMBOL_TOKEN[s]] }} />
              {s}
            </button>
          ))}
        </div>
        <label className="filter-group">
          <input type="checkbox" checked={log} onChange={(e) => setLog(e.target.checked)} />
          <span className="filter-label">Log scale</span>
        </label>
      </div>

      <ChartCard
        title="Growth of $50,000"
        subtitle={last ? series.map((s) => `${s.label} ${moneyCompact(last[s.key])}`).join(" · ") + " today" : "Rebased to the first date all selected ETFs traded"}
        legend={legend}
        loading={growth.loading}
        table={{ columns: tableCols((v) => moneyCompact(v)), rows: growth.data ?? [] }}
      >
        <TimeLineChart data={growth.data ?? []} series={series} colors={colors} yFormat={moneyCompact} height={340}
                       logScale={log} referenceY={50_000} />
      </ChartCard>

      <div className="grid grid-2">
        <ChartCard title="Rolling volatility (3 months, annualised)" subtitle="How bumpy the ride is; spikes mark crises"
                   legend={legend} loading={risk.loading} table={{ columns: tableCols((v) => pct(v)), rows: vol }}>
          <TimeLineChart data={vol} series={series} colors={colors} yFormat={(v) => pct(v, 0)} />
        </ChartCard>
        <ChartCard title="Drawdown from previous peak" subtitle="How far each ETF sat below its last high"
                   legend={legend} loading={risk.loading} table={{ columns: tableCols((v) => pct(v)), rows: dd }}>
          <TimeLineChart data={dd} series={series} colors={colors} yFormat={(v) => pct(v, 0)} referenceY={0} />
        </ChartCard>
      </div>

      <div className="grid grid-2">
        <ChartCard title="Dividend yield (trailing 12 months)" subtitle="Income paid out, as a share of price"
                   legend={legend} loading={risk.loading} table={{ columns: tableCols((v) => pct(v, 2)), rows: yld }}>
          <TimeLineChart data={yld} series={series} colors={colors} yFormat={(v) => pct(v, 1)} />
        </ChartCard>
        <section className={`card ${score.loading ? "loading" : ""}`}>
          <div className="card-head">
            <div>
              <h3 className="card-title">Scorecard · {PERIODS.find((p) => p.value === period)!.label}</h3>
              <p className="card-sub">Sharpe uses the 3-month T-bill rate; drawdown is the worst peak-to-trough fall.</p>
            </div>
          </div>
          <DataTable
            rows={[
              ...(score.data?.assets ?? []).filter((a) => symbols.includes(a.symbol)).map((a) => ({ name: a.symbol, ...a })),
              ...(score.data?.portfolios ?? []).filter((p) => p.portfolio_code.startsWith("SPY_6") || p.portfolio_code.startsWith("SPY_5"))
                .map((p) => ({ name: p.portfolio_code === "SPY_60_QQQ_40" ? "60/40 SPY/QQQ" : "50/50 SPY/QQQ", ...p })),
            ]}
            columns={[
              { key: "name", label: "" },
              { key: "cagr", label: "Return / yr", format: (v) => pct(v) },
              { key: "vol_annual", label: "Volatility", format: (v) => pct(v) },
              { key: "sharpe", label: "Sharpe", format: (v) => num(v) },
              { key: "sortino", label: "Sortino", format: (v) => num(v) },
              { key: "max_drawdown", label: "Max DD", format: (v) => pct(v, 0) },
              { key: "expense_ratio", label: "Fee", format: (v) => (v == null ? "" : pct(v, 2)) },
            ]}
          />
        </section>
      </div>
    </div>
  );
}
