import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Msg {
  role: "user" | "assistant";
  content: string;
  meta?: string;
}

const SUGGESTIONS = [
  "Does QQQ beat SPY over 3 years?",
  "What's the optimal QQQ:SPY ratio for a moderate investor with $50k?",
  "Is 60/40 really better than 50/50, statistically?",
  "What's the 95th-percentile worst drawdown for a tech-heavy portfolio?",
  "Simulate 70% QQQ with 20% in T-bills under the last-5-years scenario.",
];

export function Analyst({ aiEnabled }: { aiEnabled: boolean }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    const history = messages.map(({ role, content }) => ({ role, content }));
    setMessages((m) => [...m, { role: "user", content: q }]);
    setInput("");
    setBusy(true);
    try {
      const res = await api.ask(q, history);
      const tools = [...new Set(res.tools_used)];
      setMessages((m) => [...m, {
        role: "assistant", content: res.answer,
        meta: res.model ? `${res.model}${tools.length ? ` · used ${tools.join(", ")}` : ""}` : undefined,
      }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `Request failed: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };

  return (
    <div className="stack">
      <div>
        <h2 className="section-title">Ask the analyst</h2>
        <p className="section-sub">
          Claude answers in plain English using tools that query the live database and run fresh simulations — every number
          comes from the current pipeline run. {aiEnabled ? "" : "No ANTHROPIC_API_KEY is set, so answers come from a rule-based summary."}
        </p>
      </div>
      <section className="card chat">
        <div className="chat-log" ref={logRef} aria-live="polite">
          {!messages.length && (
            <div className="suggestions">
              {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.content}
              {m.meta && <div className="msg-meta">{m.meta}</div>}
            </div>
          ))}
          {busy && <div className="msg assistant muted">Analysing live data…</div>}
        </div>
        <form className="chat-input" onSubmit={onSubmit}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="Ask about returns, risk, the A/B test, or a what-if allocation…"
            aria-label="Question"
            rows={1}
          />
          <button className="primary-btn" disabled={busy || !input.trim()}>Ask</button>
        </form>
      </section>
      <p className="small muted">Educational tool, not personalised financial advice. Past performance does not guarantee future results.</p>
    </div>
  );
}
