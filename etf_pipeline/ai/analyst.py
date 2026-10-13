"""AI portfolio analyst: Claude answers investor questions using live pipeline data.

Claude never sees a static dump. It calls tools that query Postgres (latest Monte
Carlo, A/B test, ML forecasts, live quotes) or run a fresh what-if simulation, so
every number in an answer traces back to the current data. Without an
ANTHROPIC_API_KEY, `ask` returns a rule-based summary built from the same queries.
"""
from __future__ import annotations

import json
import logging

import anthropic
from anthropic import beta_tool
from sqlalchemy import text

from .. import db, queries
from ..config import get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the analyst inside a retail-investor tool that compares two ETFs — \
SPY (S&P 500) and QQQ (Nasdaq-100) — for a $50,000, 5-year investment.

How the numbers are produced (so you can explain them):
- Daily prices since 1999 come from Yahoo Finance; the risk-free rate is the 3-month Treasury \
yield from Alpha Vantage.
- A Monte Carlo engine simulates 10,000 five-year paths per allocation (0% to 100% QQQ in 5% \
steps) with correlated geometric Brownian motion calibrated on the full history. "Sharpe" means \
the median realised 5-year Sharpe across paths; VaR is the 1-year 95% value-at-risk.
- Each risk profile caps 1-year 95% VaR (Conservative 10%, Moderate 20%, Aggressive 30% of \
capital). When no all-equity mix fits, the tool keeps the max-Sharpe mix and adds a T-bill sleeve.
- Sensitivity scenarios re-run the frontier on 5/10/15-year windows, an equal-Sharpe prior, and \
an ML volatility forecast, because expected returns are the least reliable input.
- The A/B test compares 60% SPY / 40% QQQ against 50/50 with paired t-tests and a 1,000-iteration \
block bootstrap.

How to answer:
- Call tools for every figure you cite; never invent or recall numbers. If a tool errors, say so.
- Lead with the direct answer, then the two or three numbers that support it, in plain English \
a first-time investor understands. Use dollars on $50,000 where that is clearer than percentages.
- Be candid about uncertainty: historical drift may not repeat, and differences that are not \
statistically significant should be described as such.
- Keep answers under about 250 words. Use short paragraphs or a compact list, no tables.
- This is an educational tool, not personalised financial advice; say so briefly only when the \
user asks what they personally should do."""


def _json(obj) -> str:
    return json.dumps(obj, default=str)


# ---------------------------------------------------------------------- tools --
@beta_tool
def get_recommendations() -> str:
    """Recommended SPY/QQQ/T-bill split, in % and dollars, for each risk profile
    (Conservative, Moderate, Aggressive), with Sharpe, 1-year VaR, 95th-percentile
    drawdown, and median 5-year outcome of each recommended portfolio."""
    return _json(queries.recommendations())


@beta_tool
def get_allocation_frontier() -> str:
    """Monte Carlo results for every allocation from 0% to 100% QQQ (5% steps):
    expected return, volatility, median Sharpe/Sortino, CAGR, 5th/50th/95th percentile
    5-year value, 1-year VaR, 95th-percentile max drawdown, probability of loss."""
    keep = ["qqq_weight", "exp_return_annual", "vol_annual", "sharpe_median", "sortino_median", "cagr_median",
            "terminal_p05", "terminal_p50", "terminal_p95", "var95_1y", "var95_1y_pct", "mdd_p95",
            "prob_loss_horizon", "risk_free_annual", "calibration_start", "calibration_end", "as_of_date"]
    return _json([{k: row[k] for k in keep} for row in queries.frontier()])


@beta_tool
def get_sensitivity_scenarios() -> str:
    """How the max-Sharpe QQQ weight changes under alternative calibrations
    (full history, last 15/10/5 years, equal-Sharpe prior, ML volatility forecast),
    plus each allocation's worst-case Sharpe regret across scenarios."""
    data = queries.scenarios()
    return _json({"scenarios": data["scenarios"], "regret": data["regret"]})


@beta_tool
def get_ab_test_results() -> str:
    """A/B hypothesis test of Strategy A (60% SPY / 40% QQQ) vs Strategy B (50/50):
    test statistics, p-values, confidence intervals, win rate across rolling 3-year
    windows, and tracking error."""
    return _json(queries.ab_test()["tests"])


@beta_tool
def get_historical_scorecard(period: str) -> str:
    """Historical CAGR, volatility, Sharpe, Sortino, max drawdown and daily VaR for each
    ETF and for the backtested portfolios over a trailing period.

    Args:
        period: One of '1Y', '3Y', '5Y', '10Y', 'MAX'.
    """
    period = period.upper()
    if period not in {"1Y", "3Y", "5Y", "10Y", "MAX"}:
        return _json({"error": "period must be one of 1Y, 3Y, 5Y, 10Y, MAX"})
    return _json(queries.scorecards(period))


@beta_tool
def simulate_portfolio(qqq_weight: float, equity_fraction: float = 1.0, scenario: str = "base") -> str:
    """Run a fresh 5,000-path, 5-year Monte Carlo for a custom $50,000 portfolio.

    Args:
        qqq_weight: Share of the equity sleeve in QQQ, 0 to 1 (the rest is SPY).
        equity_fraction: Share of the $50,000 invested in SPY/QQQ, 0 to 1 (the rest earns the T-bill rate).
        scenario: Calibration to use: 'base', '15y', '10y', '5y', 'equal_sharpe' or 'ml_vol'.
    """
    if not (0 <= qqq_weight <= 1 and 0 <= equity_fraction <= 1):
        return _json({"error": "qqq_weight and equity_fraction must be between 0 and 1"})
    try:
        result = queries.simulate(qqq_weight, equity_fraction, scenario=scenario)
    except ValueError as exc:
        return _json({"error": str(exc)})
    return _json({"inputs": result["inputs"], "summary": result["summary"]})


@beta_tool
def get_volatility_forecast() -> str:
    """Next-month volatility forecasts for SPY and QQQ from the ML models, and each
    model's out-of-sample accuracy (RMSE, R², QLIKE) from walk-forward validation."""
    data = queries.ml_vol()
    return _json({"forecast": data["forecast"], "metrics": data["metrics"]})


@beta_tool
def get_live_quotes() -> str:
    """Latest streamed prices and today's % change for SPY, QQQ and the comparison ETFs."""
    return _json(queries.latest_quotes())


TOOLS = [
    get_recommendations, get_allocation_frontier, get_sensitivity_scenarios, get_ab_test_results,
    get_historical_scorecard, simulate_portfolio, get_volatility_forecast, get_live_quotes,
]


# --------------------------------------------------------------------- answer --
def _fallback_answer(question: str) -> str:
    """Rule-based summary used when no Anthropic API key is configured."""
    recs = queries.recommendations()
    tests = {t["test_name"]: t for t in queries.ab_test()["tests"]}
    lines = ["AI analyst offline (set ANTHROPIC_API_KEY to enable it). Key results from the latest run:"]
    for r in recs:
        lines.append(
            f"- {r['risk_profile']} (max {r['max_var95_1y_pct']:.0f}% 1-yr VaR): "
            f"${r['qqq_dollars']:,.0f} QQQ / ${r['spy_dollars']:,.0f} SPY / ${r['tbill_dollars']:,.0f} T-bills; "
            f"median 5-yr value ${r['terminal_p50']:,.0f}."
        )
    boot = tests.get("bootstrap_sharpe_diff")
    if boot:
        verdict = "is" if boot["significant"] else "is not"
        lines.append(
            f"- 60/40 vs 50/50: Sharpe difference {boot['estimate']:+.3f}, bootstrap p = {boot['p_value']:.2f} "
            f"— the difference {verdict} statistically significant."
        )
    return "\n".join(lines)


def _log(question: str, answer: str, model: str, usage=None) -> None:
    try:
        with db.transaction() as conn:
            conn.execute(
                text("INSERT INTO ai_interaction (question, answer, model, input_tokens, output_tokens) "
                     "VALUES (:q, :a, :m, :i, :o)"),
                {"q": question, "a": answer, "m": model,
                 "i": getattr(usage, "input_tokens", None), "o": getattr(usage, "output_tokens", None)},
            )
    except Exception:  # noqa: BLE001 - logging must never break an answer
        log.exception("Failed to log AI interaction")


def ask(question: str, history: list[dict] | None = None) -> dict:
    """Answer a question. `history` is prior turns as [{'role': 'user'|'assistant', 'content': str}].

    Prior turns are replayed as text only (no thinking blocks), which keeps every
    request valid under the API's history-editing check.
    """
    s = get_settings()
    if not s.anthropic_api_key:
        answer = _fallback_answer(question)
        _log(question, answer, "rule-based")
        return {"answer": answer, "model": "rule-based", "tools_used": []}

    messages = [
        {"role": m["role"], "content": str(m["content"])}
        for m in (history or [])[-10:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    messages.append({"role": "user", "content": question})

    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    runner = client.beta.messages.tool_runner(
        model=s.anthropic_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
        output_config={"effort": "medium"},
        cache_control={"type": "ephemeral"},
        max_iterations=8,
        # Server-side fallback: if a safety classifier declines, the API re-runs the
        # request on an appropriate fallback model within the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    tools_used: list[str] = []
    final = None
    try:
        for message in runner:
            final = message
            tools_used += [b.name for b in message.content if b.type == "tool_use"]
    except anthropic.AuthenticationError:
        return {"answer": "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", "model": None,
                "tools_used": []}
    except anthropic.RateLimitError:
        return {"answer": "The AI analyst is rate-limited right now — try again in a minute.", "model": None,
                "tools_used": []}
    except anthropic.APIStatusError as exc:
        log.exception("Claude API error")
        return {"answer": f"The AI analyst hit an API error ({exc.status_code}).", "model": None, "tools_used": []}
    except anthropic.APIConnectionError:
        return {"answer": "Could not reach the Claude API — check the network connection.", "model": None,
                "tools_used": []}

    if final is None:
        return {"answer": "No response from the AI analyst.", "model": None, "tools_used": tools_used}
    if final.stop_reason == "refusal":
        answer = "The AI analyst declined to answer this question. Try rephrasing it."
    else:
        answer = "\n\n".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            answer += "\n\n(Answer truncated.)"
    _log(question, answer, final.model, final.usage)
    return {"answer": answer, "model": final.model, "tools_used": tools_used}
