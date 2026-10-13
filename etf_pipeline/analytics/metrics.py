"""Risk/return metrics. All functions take *daily simple returns* unless noted.

Conventions: annualisation uses 252 trading days; drawdowns and VaR are returned as
positive magnitudes (a 20% drawdown -> 0.20).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def annualized_return(returns) -> float:
    r = _arr(returns)
    if r.size == 0:
        return float("nan")
    growth = np.prod(1 + r)
    return float(growth ** (TRADING_DAYS / r.size) - 1)


def annualized_vol(returns) -> float:
    return float(np.std(_arr(returns), ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe_ratio(returns, rf_daily=0.0) -> float:
    """Annualised Sharpe: mean daily excess return / its std, scaled by sqrt(252)."""
    ex = _arr(returns) - _arr(rf_daily)
    sd = np.std(ex, ddof=1)
    return float(np.mean(ex) / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else float("nan")


def sortino_ratio(returns, rf_daily=0.0) -> float:
    """Like Sharpe but only penalises downside deviation (excess returns below 0)."""
    ex = _arr(returns) - _arr(rf_daily)
    downside = np.sqrt(np.mean(np.minimum(ex, 0.0) ** 2))
    return float(np.mean(ex) / downside * np.sqrt(TRADING_DAYS)) if downside > 0 else float("nan")


def drawdown_series(values) -> np.ndarray:
    v = _arr(values)
    return v / np.maximum.accumulate(v) - 1.0


def max_drawdown(values) -> float:
    """Largest peak-to-trough fall of a value/price series, as a positive fraction."""
    return float(-drawdown_series(values).min())


def max_drawdown_from_returns(returns) -> float:
    return max_drawdown(np.concatenate([[1.0], np.cumprod(1 + _arr(returns))]))


def historical_var(returns, level: float = 0.95) -> float:
    """One-day historical VaR as a positive loss fraction."""
    return float(-np.quantile(_arr(returns), 1 - level))


def historical_cvar(returns, level: float = 0.95) -> float:
    """Expected shortfall: mean loss on days worse than the VaR threshold."""
    r = _arr(returns)
    cutoff = np.quantile(r, 1 - level)
    return float(-r[r <= cutoff].mean())


def tracking_error(returns_a, returns_b) -> float:
    return annualized_vol(_arr(returns_a) - _arr(returns_b))


def summarize(returns: pd.Series, rf_daily: pd.Series | float = 0.0) -> dict[str, float]:
    """Scorecard for one return series (used for asset/portfolio period stats)."""
    r = returns.dropna()
    rf = rf_daily.reindex(r.index).fillna(0.0) if isinstance(rf_daily, pd.Series) else rf_daily
    return {
        "total_return": float(np.prod(1 + r.to_numpy()) - 1),
        "cagr": annualized_return(r),
        "vol_annual": annualized_vol(r),
        "sharpe": sharpe_ratio(r, rf),
        "sortino": sortino_ratio(r, rf),
        "max_drawdown": max_drawdown_from_returns(r),
        "var95_daily": historical_var(r),
        "cvar95_daily": historical_cvar(r),
    }
