"""Transformations: cleaning, feature engineering, portfolio backtests, scorecards.

Pure functions on DataFrames — no database access — so they are easy to unit test.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .analytics import metrics
from .analytics.vol_model import forward_realized_vol

TRADING_DAYS = 252
PERIODS = {"1Y": 1, "3Y": 3, "5Y": 5, "10Y": 10, "MAX": None}

# The portfolios we backtest. A/B test = SPY_60_QQQ_40 (A) vs SPY_50_QQQ_50 (B).
PORTFOLIOS: dict[str, tuple[str, str, dict[str, float]]] = {
    "SPY_100": ("100% SPY", "Broad-market benchmark", {"SPY": 1.0}),
    "QQQ_100": ("100% QQQ", "Tech-heavy growth", {"QQQ": 1.0}),
    "SPY_60_QQQ_40": ("Strategy A: 60% SPY / 40% QQQ", "A/B test arm A", {"SPY": 0.6, "QQQ": 0.4}),
    "SPY_50_QQQ_50": ("Strategy B: 50% SPY / 50% QQQ", "A/B test arm B (equal weight)", {"SPY": 0.5, "QQQ": 0.5}),
}


def clean_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicates / non-positive prices, sort by date, coerce numeric types."""
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    for col in ("open", "high", "low", "close", "adj_close", "volume", "dividend"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close", "adj_close"])
    out = out[(out["close"] > 0) & (out["adj_close"] > 0)]
    out = out.drop_duplicates(subset="trade_date", keep="last").sort_values("trade_date")
    if "volume" in out:
        out["volume"] = out["volume"].fillna(0).astype("int64")
    out["trade_date"] = out["trade_date"].dt.date
    return out.reset_index(drop=True)


def daily_risk_free(rates: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Annual T-bill rates (rate_date, annual_rate) -> daily rate aligned to trading days."""
    if rates.empty:
        return pd.Series(0.0, index=index)
    s = rates.set_index(pd.to_datetime(rates["rate_date"]))["annual_rate"].astype(float).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    aligned = s.reindex(s.index.union(index)).ffill().bfill().reindex(index)
    return (1 + aligned) ** (1 / TRADING_DAYS) - 1


def asset_features(
    px: pd.DataFrame, dividends: pd.Series, rf_daily: pd.Series, initial: float = 50_000.0
) -> pd.DataFrame:
    """Per-asset daily features. px: DatetimeIndex with columns close, adj_close."""
    adj = px["adj_close"].astype(float)
    simple = adj.pct_change()
    log_r = np.log(adj).diff()
    rf = rf_daily.reindex(adj.index).fillna(0.0)
    excess = simple - rf

    div = dividends.reindex(adj.index, fill_value=0.0).astype(float)
    ttm_div = div.rolling("365D").sum()

    return pd.DataFrame(
        {
            "simple_return": simple,
            "log_return": log_r,
            "growth_of_50k": initial * adj / adj.iloc[0],
            "drawdown": adj / adj.cummax() - 1,
            "rolling_vol_63d": log_r.rolling(63).std() * np.sqrt(TRADING_DAYS),
            "rolling_return_252d": adj / adj.shift(TRADING_DAYS) - 1,
            "rolling_sharpe_252d": excess.rolling(TRADING_DAYS).mean()
            / excess.rolling(TRADING_DAYS).std()
            * np.sqrt(TRADING_DAYS),
            "ttm_dividend_yield": ttm_div / px["close"].astype(float),
            "fwd_realized_vol_21d": forward_realized_vol(log_r),
        },
        index=adj.index,
    )


def backtest(prices: pd.DataFrame, weights: dict[str, float], initial: float = 50_000.0) -> pd.Series:
    """Value of a portfolio rebalanced to target weights at each month-end close.

    prices: DatetimeIndex x symbols of adjusted closes (dividends reinvested).
    """
    px = prices[list(weights)].dropna()
    w = np.array(list(weights.values()), dtype=float)
    rets = px.pct_change().iloc[1:].to_numpy()
    months = px.index[1:].to_period("M")

    holdings = initial * w
    values = np.empty(len(px))
    values[0] = initial
    current = months[0] if len(months) else None
    for i, (month, r) in enumerate(zip(months, rets), start=1):
        if month != current:            # first day of a new month: rebalance at prior close
            holdings = holdings.sum() * w
            current = month
        holdings = holdings * (1 + r)
        values[i] = holdings.sum()
    return pd.Series(values, index=px.index, name="value")


def portfolio_daily_frame(values: pd.Series) -> pd.DataFrame:
    ret = values.pct_change()
    return pd.DataFrame(
        {
            "value": values,
            "daily_return": ret,
            "drawdown": values / values.cummax() - 1,
            "rolling_vol_63d": np.log(values).diff().rolling(63).std() * np.sqrt(TRADING_DAYS),
        }
    )


def period_stats(returns: pd.Series, rf_daily: pd.Series) -> list[dict]:
    """Scorecard for each trailing period that the history fully covers."""
    r = returns.dropna()
    if r.empty:
        return []
    end = r.index.max()
    rows = []
    for label, years in PERIODS.items():
        if years is None:
            window = r
        else:
            start = end - pd.DateOffset(years=years)
            if r.index.min() > start + pd.Timedelta(days=7):
                continue  # not enough history for this period
            window = r[r.index > start]
        rows.append(
            {
                "period": label,
                "period_start": window.index.min().date(),
                "period_end": end.date(),
                **metrics.summarize(window, rf_daily),
            }
        )
    return rows
