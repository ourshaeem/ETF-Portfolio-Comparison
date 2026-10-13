"""Read-side service layer shared by the REST API and the AI analyst's tools.

Every function returns plain JSON-serialisable Python objects.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from . import db
from .analytics import monte_carlo
from .config import get_settings

TRADING_DAYS = 252


def _records(df: pd.DataFrame) -> list[dict]:
    def conv(v):
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return None
        if isinstance(v, (pd.Timestamp, datetime)):
            return v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, np.generic):
            return conv(v.item())
        return v

    return [{k: conv(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _weekly(df: pd.DataFrame, date_col: str, group: str | None = None) -> pd.DataFrame:
    """Thin daily rows to the last real trading day of each week (plus each series' first
    row), keeping actual dates — lighter payloads for multi-decade charts."""
    df = df.sort_values(date_col)
    week = df[date_col].dt.to_period("W-FRI")
    keys = [df[group], week] if group else [week]
    is_last = df.groupby(keys)[date_col].transform("max") == df[date_col]
    first = (df.groupby(df[group])[date_col].transform("min") if group else df[date_col].min()) == df[date_col]
    return df[is_last | first]


# ---------------------------------------------------------------- history --
def assets() -> list[dict]:
    return _records(db.read_sql(
        """SELECT symbol, name, asset_class, category, fund_family, expense_ratio::float AS expense_ratio,
                  inception_date, is_core, updated_at
           FROM asset ORDER BY is_core DESC, symbol"""))


def growth_series(symbols: list[str], start: str | None = None, initial: float = 50_000.0) -> list[dict]:
    """Growth of `initial` per ETF, rebased to the first common date on/after `start`."""
    df = db.read_sql(
        """SELECT a.symbol, p.trade_date, p.adj_close::float AS adj_close
           FROM price_daily p JOIN asset a USING (asset_id)
           WHERE a.symbol = ANY(:symbols) AND (CAST(:start AS date) IS NULL OR p.trade_date >= CAST(:start AS date))
           ORDER BY p.trade_date""",
        {"symbols": symbols, "start": start},
        parse_dates=["trade_date"],
    )
    if df.empty:
        return []
    wide = df.pivot(index="trade_date", columns="symbol", values="adj_close").dropna()
    out = (initial * wide / wide.iloc[0]).round(2).rename_axis("date").reset_index()
    if len(out) > 1500:
        out = _weekly(out, "date")
    out["date"] = out["date"].dt.date
    return _records(out)


def rolling_risk(symbols: list[str], start: str | None = None) -> list[dict]:
    df = db.read_sql(
        """SELECT symbol, trade_date, rolling_vol_63d, drawdown, rolling_sharpe_252d, ttm_dividend_yield
           FROM tv_price_history
           WHERE symbol = ANY(:symbols) AND (CAST(:start AS date) IS NULL OR trade_date >= CAST(:start AS date))
           ORDER BY trade_date""",
        {"symbols": symbols, "start": start},
        parse_dates=["trade_date"],
    )
    if len(df) > 3000:
        df = _weekly(df, "trade_date", "symbol")
    df["trade_date"] = df["trade_date"].dt.date
    return _records(df.dropna(subset=["rolling_vol_63d"]))


def scorecards(period: str = "MAX") -> dict:
    a = db.read_sql("SELECT * FROM tv_asset_scorecard WHERE period = :p ORDER BY is_core DESC, symbol", {"p": period})
    p = db.read_sql("SELECT * FROM tv_portfolio_scorecard WHERE period = :p ORDER BY portfolio_code", {"p": period})
    return {"period": period, "assets": _records(a), "portfolios": _records(p)}


def portfolio_history(codes: list[str] | None = None) -> list[dict]:
    df = db.read_sql(
        """SELECT portfolio_code, trade_date, value, drawdown FROM tv_portfolio_history
           WHERE CAST(:codes AS text[]) IS NULL OR portfolio_code = ANY(:codes) ORDER BY trade_date""",
        {"codes": codes}, parse_dates=["trade_date"],
    )
    df = _weekly(df, "trade_date", "portfolio_code")
    df["trade_date"] = df["trade_date"].dt.date
    return _records(df.dropna())


# ------------------------------------------------------------- Monte Carlo --
def frontier() -> list[dict]:
    return _records(db.read_sql("SELECT * FROM tv_mc_frontier ORDER BY qqq_weight"))


def fan(qqq_weight: float) -> list[dict]:
    df = db.read_sql(
        "SELECT * FROM tv_mc_fan WHERE qqq_weight = :w AND step % 5 = 0 ORDER BY step",
        {"w": round(qqq_weight, 2)},
    )
    return _records(df)


def terminal_histogram(qqq_weight: float, bins: int = 40) -> dict:
    df = db.read_sql(
        "SELECT terminal_value, max_drawdown FROM tv_mc_terminal WHERE qqq_weight = :w", {"w": round(qqq_weight, 2)}
    )
    if df.empty:
        return {"bins": [], "drawdown_bins": []}
    tv = df["terminal_value"].clip(upper=df["terminal_value"].quantile(0.995))
    counts, edges = np.histogram(tv, bins=bins)
    dd_counts, dd_edges = np.histogram(df["max_drawdown"], bins=bins, range=(0, 1))
    return {
        "bins": [{"lo": float(edges[i]), "hi": float(edges[i + 1]), "count": int(c)} for i, c in enumerate(counts)],
        "drawdown_bins": [{"lo": float(dd_edges[i]), "hi": float(dd_edges[i + 1]), "count": int(c)}
                          for i, c in enumerate(dd_counts)],
    }


def recommendations() -> list[dict]:
    return _records(db.read_sql("SELECT * FROM tv_recommendation ORDER BY max_var95_1y_pct"))


def scenarios() -> dict:
    sc = db.read_sql("SELECT * FROM tv_mc_scenarios ORDER BY scenario_code, qqq_weight")
    regret = db.read_sql("SELECT * FROM tv_mc_regret ORDER BY qqq_weight")
    meta = sc.drop_duplicates("scenario_code")[[
        "scenario_code", "scenario", "description", "calibration_start", "calibration_end",
        "spy_mu", "spy_sigma", "qqq_mu", "qqq_sigma", "correlation", "max_sharpe_weight"]]
    return {
        "scenarios": _records(meta),
        "frontiers": _records(sc[["scenario_code", "qqq_weight", "sharpe_median", "var95_1y_pct", "mdd_p95", "cagr_median"]]),
        "regret": _records(regret),
    }


def run_info() -> dict:
    df = db.read_sql(
        """SELECT run_type, max(run_id) AS run_id, max(finished_at) AS finished_at, max(as_of_date) AS as_of_date
           FROM model_run WHERE status = 'success' GROUP BY run_type""")
    cfg = db.read_sql("SELECT * FROM mc_run_config WHERE run_id = latest_run_id('monte_carlo')")
    return {"runs": _records(df), "monte_carlo_config": _records(cfg)[0] if not cfg.empty else None}


def _calibration_for(scenario: str) -> tuple[monte_carlo.GBMCalibration, float]:
    row = db.read_sql(
        """SELECT sc.*, c.risk_free_annual FROM mc_scenario sc JOIN mc_run_config c USING (run_id)
           WHERE sc.run_id = latest_run_id('monte_carlo') AND sc.scenario_code = :s""",
        {"s": scenario},
    )
    if row.empty:
        raise ValueError(f"Unknown scenario '{scenario}'")
    r = row.iloc[0]
    mu = np.array([r["spy_mu"], r["qqq_mu"]])
    sig = np.array([r["spy_sigma"], r["qqq_sigma"]]) / np.sqrt(TRADING_DAYS)
    corr = np.array([[1.0, r["correlation"]], [r["correlation"], 1.0]])
    cov = corr * np.outer(sig, sig)
    cal = monte_carlo.GBMCalibration(
        ("SPY", "QQQ"), mu / TRADING_DAYS - 0.5 * np.diag(cov), cov, r["calibration_start"], r["calibration_end"]
    )
    return cal, float(r["risk_free_annual"])


def simulate(
    qqq_weight: float,
    equity_fraction: float = 1.0,
    years: int = 5,
    initial: float = 50_000.0,
    n_paths: int = 5_000,
    scenario: str = "base",
) -> dict:
    """On-demand what-if simulation using the latest stored calibration."""
    cal, rf = _calibration_for(scenario)
    asset_r = monte_carlo.simulate_asset_returns(cal, n_paths, years * TRADING_DAYS, get_settings().seed)
    res = monte_carlo.simulate_allocation(asset_r, cal, qqq_weight, initial, rf, equity_fraction=equity_fraction)
    fan_df = res.fan[res.fan["step"] % 5 == 0]
    return {
        "inputs": {"qqq_weight": qqq_weight, "equity_fraction": equity_fraction, "years": years,
                   "initial": initial, "n_paths": n_paths, "scenario": scenario, "risk_free": rf},
        "summary": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in res.summary.items()},
        "fan": _records(fan_df),
    }


def recommend_for(max_var_pct: float, n_paths: int | None = None) -> dict:
    """Recommendation for an arbitrary VaR tolerance (the allocator slider), using the
    stored frontier and the same max-Sharpe + T-bill-sleeve rule as the pipeline."""
    summary = db.read_sql("SELECT * FROM tv_mc_frontier ORDER BY qqq_weight")
    if summary.empty:
        raise ValueError("No Monte Carlo run yet")
    cfg = summary.iloc[0]
    initial, years = float(cfg["initial_investment"]), int(cfg["horizon_years"])
    cal, rf = _calibration_for("base")
    # Same path count and seed as the pipeline, so preset profiles match the Overview exactly
    n = n_paths or int(cfg["n_paths"])
    asset_r = monte_carlo.simulate_asset_returns(cal, n, years * TRADING_DAYS, get_settings().seed)

    def sim(w: float, f: float) -> dict:
        return monte_carlo.simulate_allocation(asset_r, cal, w, initial, rf, detail=False, equity_fraction=f).summary

    rec = monte_carlo.recommend(summary, max_var_pct, sim)
    w, f = rec["recommended_qqq_weight"], rec["equity_fraction"]
    rec.update({
        "max_var95_1y_pct": max_var_pct,
        "qqq_weight": f * w, "spy_weight": f * (1 - w), "tbill_weight": 1 - f,
        "qqq_dollars": initial * f * w, "spy_dollars": initial * f * (1 - w), "tbill_dollars": initial * (1 - f),
    })
    return {k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in rec.items()}


# ----------------------------------------------------------------- A/B test --
def ab_test() -> dict:
    return {
        "tests": _records(db.read_sql("SELECT * FROM tv_ab_tests")),
        "rolling": _records(db.read_sql("SELECT * FROM tv_ab_rolling ORDER BY window_end")),
        "bootstrap": _records(db.read_sql("SELECT * FROM tv_ab_bootstrap ORDER BY iteration")),
    }


# ---------------------------------------------------------------- ML model --
def ml_vol() -> dict:
    pred = db.read_sql(
        """SELECT symbol, model_name, trade_date, predicted_vol, realized_vol FROM tv_ml_predictions
           WHERE model_name IN ('har_gbm_hybrid', 'naive_rv21') ORDER BY trade_date""",
        parse_dates=["trade_date"],
    )
    if not pred.empty:
        pred["series"] = pred["symbol"] + "|" + pred["model_name"]
        pred = _weekly(pred, "trade_date", "series").drop(columns="series")
        pred["trade_date"] = pred["trade_date"].dt.date
    return {
        "metrics": _records(db.read_sql("SELECT * FROM tv_ml_metrics WHERE fold = 0 ORDER BY symbol, rmse")),
        "forecast": _records(db.read_sql("SELECT * FROM tv_ml_forecast ORDER BY symbol, model_name")),
        "predictions": _records(pred.dropna()),
    }


# ---------------------------------------------------------------- live data --
def latest_quotes() -> list[dict]:
    return _records(db.read_sql(
        """SELECT DISTINCT ON (symbol) symbol, quote_ts, price, previous_close, change_pct
           FROM tv_live_quotes ORDER BY symbol, quote_ts DESC"""))
