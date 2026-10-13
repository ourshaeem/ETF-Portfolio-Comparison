import { useEffect, useState } from "react";
import { api } from "./api";
import { useApi, useLiveQuotes } from "./hooks";
import { useColors } from "./theme";
import { LiveTicker } from "./components/LiveTicker";
import { Overview } from "./views/Overview";
import { Performance } from "./views/Performance";
import { RiskMonteCarlo } from "./views/RiskMonteCarlo";
import { ABTest } from "./views/ABTest";
import { Allocator } from "./views/Allocator";
import { Analyst } from "./views/Analyst";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "performance", label: "Performance" },
  { id: "risk", label: "Risk & Monte Carlo" },
  { id: "ab", label: "A/B test" },
  { id: "allocator", label: "Allocator" },
  { id: "analyst", label: "Ask the analyst" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function readTab(): Tab {
  const h = location.hash.slice(1);
  return (TABS.find((t) => t.id === h)?.id ?? "overview") as Tab;
}

export default function App() {
  const [tab, setTab] = useState<Tab>(readTab);
  const [theme, setTheme] = useState<string | null>(() => {
    try { return localStorage.getItem("theme"); } catch { return null; }
  });
  const colors = useColors();
  const live = useLiveQuotes();
  const meta = useApi(api.meta);

  useEffect(() => {
    const onHash = () => setTab(readTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try { theme ? localStorage.setItem("theme", theme) : localStorage.removeItem("theme"); } catch { /* private mode */ }
  }, [theme]);

  const isDark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const asOf = meta.data?.runs?.find((r: any) => r.run_type === "monte_carlo")?.as_of_date;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">
            SPY vs QQQ <small>$50,000 allocator{asOf ? ` · data as of ${asOf}` : ""}</small>
          </div>
          <LiveTicker live={live} />
          <button className="icon-btn" onClick={() => setTheme(isDark ? "light" : "dark")} aria-label="Toggle dark mode">
            {isDark ? "☀" : "☾"}
          </button>
        </div>
        <nav className="tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" className="tab" aria-selected={tab === t.id}
                    onClick={() => (location.hash = t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {meta.error && <section className="card down">API unreachable: {meta.error}. Is the api service running?</section>}
        {tab === "overview" && <Overview colors={colors} live={live} meta={meta.data} />}
        {tab === "performance" && <Performance colors={colors} />}
        {tab === "risk" && <RiskMonteCarlo colors={colors} />}
        {tab === "ab" && <ABTest colors={colors} />}
        {tab === "allocator" && <Allocator colors={colors} />}
        {tab === "analyst" && <Analyst aiEnabled={!!meta.data?.ai_enabled} />}
      </main>
    </div>
  );
}
