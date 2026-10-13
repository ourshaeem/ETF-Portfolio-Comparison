"""Primary extractor: Yahoo Finance via yfinance (no API key needed)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

PRICE_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividend",
}


def _retry(fn, attempts: int = 3, base_delay: float = 2.0):
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - network/ratelimit errors vary by yfinance version
            if i == attempts - 1:
                raise
            delay = base_delay * 2**i
            log.warning("yfinance call failed (%s); retrying in %.0fs", exc, delay)
            time.sleep(delay)


def fetch_prices(symbol: str, start: str) -> pd.DataFrame:
    """Daily OHLCV + adjusted close + dividends. Columns: trade_date, open, ..., dividend."""

    def _get():
        hist = yf.Ticker(symbol).history(start=start, auto_adjust=False, actions=True)
        if hist.empty:
            raise ValueError(f"yfinance returned no rows for {symbol}")
        return hist

    hist = _retry(_get)
    df = hist.rename(columns=PRICE_COLUMNS)
    df = df[[c for c in PRICE_COLUMNS.values() if c in df.columns]]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.rename_axis("trade_date").reset_index()
    df["trade_date"] = df["trade_date"].dt.date
    if "dividend" not in df:
        df["dividend"] = 0.0
    return df


@dataclass
class FundProfile:
    symbol: str
    name: str | None = None
    asset_class: str = "ETF"
    category: str | None = None
    fund_family: str | None = None
    expense_ratio: float | None = None
    inception_date: date | None = None
    total_assets: float | None = None
    sector_weights: dict[str, float] = field(default_factory=dict)
    top_holdings: list[tuple[str, str, float]] = field(default_factory=list)  # (symbol, name, weight)


def fetch_profile(symbol: str) -> FundProfile:
    t = yf.Ticker(symbol)
    info = _retry(lambda: t.info) or {}
    quote_type = info.get("quoteType", "")
    expense = info.get("netExpenseRatio")  # reported in percent, e.g. 0.18
    inception = info.get("fundInceptionDate")
    profile = FundProfile(
        symbol=symbol,
        name=info.get("longName") or info.get("shortName"),
        asset_class="INDEX" if quote_type == "INDEX" or symbol.startswith("^") else "ETF",
        category=info.get("category"),
        fund_family=info.get("fundFamily"),
        expense_ratio=expense / 100 if expense is not None else None,
        inception_date=datetime.fromtimestamp(inception, tz=timezone.utc).date() if inception else None,
        total_assets=info.get("totalAssets"),
    )
    if profile.asset_class == "ETF":
        try:
            fd = t.funds_data
            profile.sector_weights = {k: float(v) for k, v in (fd.sector_weightings or {}).items()}
            top = fd.top_holdings
            if top is not None and not top.empty:
                profile.top_holdings = [
                    (str(sym), str(row.get("Name", sym)), float(row["Holding Percent"]))
                    for sym, row in top.iterrows()
                ]
            if profile.expense_ratio is None and fd.fund_operations is not None:
                ops = fd.fund_operations
                if "Annual Report Expense Ratio" in ops.index:
                    profile.expense_ratio = float(ops.loc["Annual Report Expense Ratio"].iloc[0])
        except Exception as exc:  # noqa: BLE001 - funds_data is best-effort
            log.warning("funds_data unavailable for %s: %s", symbol, exc)
    return profile


def fetch_risk_free(start: str, symbol: str = "^IRX") -> pd.DataFrame:
    """13-week T-bill yield (percent) -> decimal annual rate. Columns: rate_date, annual_rate."""
    df = fetch_prices(symbol, start)
    return pd.DataFrame({"rate_date": df["trade_date"], "annual_rate": df["close"] / 100.0})


def fetch_quotes(symbols: list[str]) -> list[dict]:
    """Latest price + previous close per symbol (used by the polling streamer)."""
    out = []
    for sym in symbols:
        try:
            fi = yf.Ticker(sym).fast_info
            out.append({"symbol": sym, "price": float(fi["lastPrice"]), "previous_close": float(fi["previousClose"])})
        except Exception as exc:  # noqa: BLE001
            log.warning("quote failed for %s: %s", sym, exc)
    return out
