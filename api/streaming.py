"""Live quote streaming.

    Yahoo push WebSocket ──ticks──► QuoteHub ──JSON (≤1/s)──► browser WebSockets (/ws/quotes)
           │ (market closed / socket down)        │
           └──► fast_info polling fallback        └──► live_quote table (≤1 row/symbol/15s)
                                                        └──► Tableau live connection

Each broadcast also values the recommended portfolio for every risk profile at
today's prices, so the front-end shows the live P&L of the $50k recommendation.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from datetime import datetime, timezone

import certifi
import pandas as pd
import yfinance as yf
from fastapi import WebSocket

from etf_pipeline import db, queries
from etf_pipeline.extract.yfinance_client import fetch_quotes

# python.org builds of Python on macOS ship without a CA bundle; Docker images are fine.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

log = logging.getLogger(__name__)

BROADCAST_INTERVAL = 1.0     # seconds between pushes to browsers
PERSIST_INTERVAL = 15.0      # seconds between DB writes per symbol
STALE_AFTER = 60.0           # no upstream tick for this long -> poll instead
RECS_REFRESH = 600.0         # reload recommendations every 10 minutes


class QuoteHub:
    def __init__(self, symbols: list[str], poll_seconds: int, initial: float = 50_000.0):
        self.symbols = symbols
        self.poll_seconds = poll_seconds
        self.initial = initial
        self.clients: set[WebSocket] = set()
        self.quotes: dict[str, dict] = {}
        self.source = "starting"
        self._dirty = False
        self._last_tick = 0.0
        self._last_persist: dict[str, float] = {}
        self._recs: list[dict] = []
        self._recs_loaded = 0.0
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------ lifecycle --
    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._upstream(), name="yahoo-ws"),
            asyncio.create_task(self._poller(), name="poller"),
            asyncio.create_task(self._broadcaster(), name="broadcaster"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    # -------------------------------------------------------------- clients --
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        await ws.send_json(await self.snapshot())

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    # ------------------------------------------------------------- ingestion --
    def _update(self, symbol: str, price: float, previous_close: float | None, source: str) -> None:
        q = self.quotes.get(symbol, {})
        prev = previous_close if previous_close else q.get("previous_close")
        self.quotes[symbol] = {
            "symbol": symbol,
            "price": price,
            "previous_close": prev,
            "change_pct": (price / prev - 1) if prev else None,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.source = source
        self._dirty = True

    async def _upstream(self) -> None:
        """Subscribe to Yahoo's push feed; reconnect with backoff on failure."""
        backoff = 2
        while True:
            try:
                async with yf.AsyncWebSocket(verbose=False) as ws:
                    await ws.subscribe(self.symbols)
                    backoff = 2

                    async def on_message(msg: dict) -> None:
                        sym, price = msg.get("id"), msg.get("price")
                        if sym in self.symbols and price:
                            change = msg.get("change")
                            prev = price - change if change is not None else None
                            self._last_tick = time.monotonic()
                            self._update(sym, float(price), prev, "yahoo-websocket")

                    await ws.listen(on_message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Upstream WebSocket dropped (%s); reconnecting in %ss", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def _poller(self) -> None:
        """Poll quotes whenever the push feed is quiet (market closed or socket down)."""
        while True:
            if time.monotonic() - self._last_tick > STALE_AFTER:
                try:
                    for q in await asyncio.to_thread(fetch_quotes, self.symbols):
                        self._update(q["symbol"], q["price"], q["previous_close"], "polling")
                except Exception:  # noqa: BLE001
                    log.exception("Quote polling failed")
            await asyncio.sleep(self.poll_seconds)

    # -------------------------------------------------------------- outputs --
    async def _recommendations(self) -> list[dict]:
        if time.monotonic() - self._recs_loaded > RECS_REFRESH:
            try:
                self._recs = await asyncio.to_thread(queries.recommendations)
                self._recs_loaded = time.monotonic()
            except Exception:  # noqa: BLE001 - tables may not exist before the first pipeline run
                self._recs = []
        return self._recs

    async def snapshot(self) -> dict:
        def day_change(sym: str) -> float:
            return self.quotes.get(sym, {}).get("change_pct") or 0.0

        portfolios = []
        for r in await self._recommendations():
            pnl_pct = r["qqq_weight"] * day_change("QQQ") + r["spy_weight"] * day_change("SPY")
            portfolios.append({
                "risk_profile": r["risk_profile"],
                "qqq_weight": r["qqq_weight"], "spy_weight": r["spy_weight"], "tbill_weight": r["tbill_weight"],
                "day_pnl_pct": pnl_pct,
                "day_pnl": self.initial * pnl_pct,
            })
        return {
            "type": "quotes",
            "source": self.source,
            "ts": datetime.now(timezone.utc).isoformat(),
            "quotes": sorted(self.quotes.values(), key=lambda q: self.symbols.index(q["symbol"])),
            "portfolios": portfolios,
        }

    async def _broadcaster(self) -> None:
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL)
            if not self._dirty:
                continue
            self._dirty = False
            msg = await self.snapshot()
            for ws in list(self.clients):
                try:
                    await ws.send_json(msg)
                except Exception:  # noqa: BLE001 - client went away
                    self.disconnect(ws)
            await asyncio.to_thread(self._persist)

    def _persist(self) -> None:
        now = time.monotonic()
        due = [q for s, q in self.quotes.items() if now - self._last_persist.get(s, 0) >= PERSIST_INTERVAL]
        if not due:
            return
        try:
            with db.transaction() as conn:
                ids = db.asset_ids(conn)
                rows = pd.DataFrame([
                    {"asset_id": ids[q["symbol"]], "quote_ts": q["ts"], "price": q["price"],
                     "previous_close": q["previous_close"]}
                    for q in due if q["symbol"] in ids
                ])
                db.upsert_df(conn, "live_quote", rows, ["asset_id", "quote_ts"])
            for q in due:
                self._last_persist[q["symbol"]] = now
        except Exception:  # noqa: BLE001 - DB hiccups must not stop the stream
            log.exception("Persisting live quotes failed")
