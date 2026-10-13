// Thin typed client for the FastAPI service. Rows are loosely typed records: the
// server owns the schema (see etf_pipeline/queries.py) and every view reads by name.
export type Row = Record<string, any>;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${detail}`.trim());
  }
  return res.json() as Promise<T>;
}

export const api = {
  meta: () => request<Row>("/api/meta"),
  growth: (symbols: string[], start?: string) =>
    request<Row[]>(`/api/history/growth?symbols=${symbols.join(",")}${start ? `&start=${start}` : ""}`),
  risk: (symbols: string[], start?: string) =>
    request<Row[]>(`/api/history/risk?symbols=${symbols.join(",")}${start ? `&start=${start}` : ""}`),
  scorecard: (period: string) => request<{ assets: Row[]; portfolios: Row[] }>(`/api/history/scorecard?period=${period}`),
  frontier: () => request<Row[]>("/api/mc/frontier"),
  fan: (w: number) => request<Row[]>(`/api/mc/fan?qqq_weight=${w}`),
  terminal: (w: number) => request<{ bins: Row[]; drawdown_bins: Row[] }>(`/api/mc/terminal?qqq_weight=${w}`),
  recommendations: () => request<Row[]>("/api/mc/recommendations"),
  recommend: (maxVar: number) => request<Row>(`/api/mc/recommend?max_var_pct=${maxVar}`),
  scenarios: () => request<{ scenarios: Row[]; frontiers: Row[]; regret: Row[] }>("/api/mc/scenarios"),
  simulate: (body: Row) =>
    request<{ inputs: Row; summary: Row; fan: Row[] }>("/api/mc/simulate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  ab: () => request<{ tests: Row[]; rolling: Row[]; bootstrap: Row[] }>("/api/ab"),
  mlVol: () => request<{ metrics: Row[]; forecast: Row[]; predictions: Row[] }>("/api/ml/volatility"),
  ask: (question: string, history: { role: string; content: string }[]) =>
    request<{ answer: string; model: string | null; tools_used: string[] }>("/api/ai/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question, history }),
    }),
};
