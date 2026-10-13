"""Alpha Vantage extractor.

Roles in the pipeline:
  * primary source for the risk-free rate (official 3-month Treasury constant maturity)
  * fallback for prices and ETF profiles when yfinance fails
Free tier = 25 requests/day, so calls are spaced out and used sparingly.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from .yfinance_client import FundProfile

log = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
_MIN_INTERVAL = 1.2  # seconds between calls (free tier burst limit)
_last_call = 0.0


class AlphaVantageError(RuntimeError):
    pass


def _get(params: dict, api_key: str) -> dict:
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(BASE_URL, params={**params, "apikey": api_key}, timeout=30)
    _last_call = time.monotonic()
    resp.raise_for_status()
    payload = resp.json()
    # Alpha Vantage returns HTTP 200 with an explanatory key on errors / rate limits
    for key in ("Error Message", "Note", "Information"):
        if key in payload:
            raise AlphaVantageError(f"{params.get('function')}: {payload[key]}")
    return payload


def fetch_treasury_yield(api_key: str, maturity: str = "3month") -> pd.DataFrame:
    """Daily Treasury yield -> columns rate_date, annual_rate (decimal)."""
    payload = _get({"function": "TREASURY_YIELD", "interval": "daily", "maturity": maturity}, api_key)
    df = pd.DataFrame(payload["data"])
    df["annual_rate"] = pd.to_numeric(df["value"], errors="coerce") / 100.0  # '.' marks holidays
    df["rate_date"] = pd.to_datetime(df["date"]).dt.date
    return df.dropna(subset=["annual_rate"])[["rate_date", "annual_rate"]].sort_values("rate_date")


def fetch_prices(symbol: str, api_key: str) -> pd.DataFrame:
    """Daily OHLCV. Alpha Vantage's free daily series is split- but not dividend-adjusted,
    so adj_close = close here (flagged via source_id in price_daily)."""
    try:
        payload = _get({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "full"}, api_key)
    except AlphaVantageError as exc:
        log.warning("Full history unavailable on free tier (%s); using compact (100 days)", exc)
        payload = _get({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact"}, api_key)
    series = payload["Time Series (Daily)"]
    df = pd.DataFrame.from_dict(series, orient="index").astype(float)
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index)
    df = df.sort_index().rename_axis("trade_date").reset_index()
    df["trade_date"] = df["trade_date"].dt.date
    df["adj_close"] = df["close"]
    df["dividend"] = 0.0
    return df


def fetch_profile(symbol: str, api_key: str) -> FundProfile:
    payload = _get({"function": "ETF_PROFILE", "symbol": symbol}, api_key)

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    sectors = {
        s["sector"].lower().replace(" ", "_"): _num(s["weight"]) or 0.0 for s in payload.get("sectors", [])
    }
    holdings = [
        (h["symbol"], h.get("description", h["symbol"]), _num(h["weight"]) or 0.0)
        for h in payload.get("holdings", [])[:10]
    ]
    inception = payload.get("inception_date")
    return FundProfile(
        symbol=symbol,
        expense_ratio=_num(payload.get("net_expense_ratio")),
        total_assets=_num(payload.get("net_assets")),
        inception_date=pd.to_datetime(inception).date() if inception else None,
        sector_weights=sectors,
        top_holdings=holdings,
    )
