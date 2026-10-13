"""FastAPI service: REST endpoints for the web app, a WebSocket quote stream, and the AI analyst."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from etf_pipeline import db, queries
from etf_pipeline.ai import analyst
from etf_pipeline.config import get_settings

from .streaming import QuoteHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("yfinance").setLevel(logging.WARNING)

settings = get_settings()
hub = QuoteHub(list(settings.price_symbols), settings.quote_poll_seconds, settings.initial_investment)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(db.wait_for_db)
    await asyncio.to_thread(db.ensure_schema)
    await hub.start()
    yield
    await hub.stop()


app = FastAPI(title="ETF Portfolio Comparison API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Period = Literal["1Y", "3Y", "5Y", "10Y", "MAX"]


def _symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


# ------------------------------------------------------------------- meta --
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ai_enabled": bool(settings.anthropic_api_key), "stream_source": hub.source}


@app.get("/api/meta")
def meta() -> dict:
    return {**queries.run_info(), "assets": queries.assets(), "ai_enabled": bool(settings.anthropic_api_key)}


# ---------------------------------------------------------------- history --
@app.get("/api/history/growth")
def growth(symbols: str = "SPY,QQQ", start: str | None = None) -> list[dict]:
    return queries.growth_series(_symbols(symbols), start, settings.initial_investment)


@app.get("/api/history/risk")
def risk(symbols: str = "SPY,QQQ", start: str | None = None) -> list[dict]:
    return queries.rolling_risk(_symbols(symbols), start)


@app.get("/api/history/scorecard")
def scorecard(period: Period = "MAX") -> dict:
    return queries.scorecards(period)


@app.get("/api/history/portfolios")
def portfolios(codes: str | None = None) -> list[dict]:
    return queries.portfolio_history(_symbols(codes) if codes else None)


# ------------------------------------------------------------ Monte Carlo --
@app.get("/api/mc/frontier")
def frontier() -> list[dict]:
    return queries.frontier()


@app.get("/api/mc/fan")
def fan(qqq_weight: float = Query(0.5, ge=0, le=1)) -> list[dict]:
    return queries.fan(qqq_weight)


@app.get("/api/mc/terminal")
def terminal(qqq_weight: float = Query(0.5, ge=0, le=1)) -> dict:
    return queries.terminal_histogram(qqq_weight)


@app.get("/api/mc/recommendations")
def recommendations() -> list[dict]:
    return queries.recommendations()


@app.get("/api/mc/recommend")
async def recommend(max_var_pct: float = Query(20, ge=1, le=60)) -> dict:
    try:
        return await asyncio.to_thread(queries.recommend_for, max_var_pct)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/mc/scenarios")
def scenarios() -> dict:
    return queries.scenarios()


class SimulationRequest(BaseModel):
    qqq_weight: float = Field(0.5, ge=0, le=1)
    equity_fraction: float = Field(1.0, ge=0, le=1)
    years: int = Field(5, ge=1, le=30)
    initial: float = Field(50_000, gt=0, le=10_000_000)
    n_paths: int = Field(5_000, ge=500, le=20_000)
    scenario: str = "base"


@app.post("/api/mc/simulate")
async def simulate(req: SimulationRequest) -> dict:
    try:
        return await asyncio.to_thread(queries.simulate, **req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------- A/B test & ML --
@app.get("/api/ab")
def ab() -> dict:
    return queries.ab_test()


@app.get("/api/ml/volatility")
def ml_volatility() -> dict:
    return queries.ml_vol()


# --------------------------------------------------------------------- AI --
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


@app.post("/api/ai/ask")
async def ask(req: AskRequest) -> dict:
    return await asyncio.to_thread(analyst.ask, req.question, [t.model_dump() for t in req.history])


# ---------------------------------------------------------------- live --
@app.get("/api/live/quotes")
async def live_quotes() -> dict:
    return await hub.snapshot()


@app.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; client messages are ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)
