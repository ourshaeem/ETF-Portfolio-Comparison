"""Runtime configuration, read from environment variables (and .env when present)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip())


def _default_db_url() -> str:
    user = os.getenv("POSTGRES_USER", "etf")
    password = os.getenv("POSTGRES_PASSWORD", "etf")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "etf")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL") or _default_db_url())
    alpha_vantage_key: str | None = field(default_factory=lambda: os.getenv("ALPHA_VANTAGE_API_KEY") or None)
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))

    # Universe. SPY & QQQ are the question; extras power Dashboard 1's ETF toggle;
    # ^VIX is a feature for the ML volatility model.
    core_symbols: tuple[str, ...] = ("SPY", "QQQ")
    extra_symbols: tuple[str, ...] = field(default_factory=lambda: _env_list("EXTRA_SYMBOLS", "DIA,IWM,VTI"))
    index_symbols: tuple[str, ...] = ("^VIX",)
    risk_free_symbol: str = "^IRX"  # 13-week T-bill (yfinance fallback for risk-free rate)

    # QQQ inception — includes the dot-com crash, GFC, COVID and 2022 (a full cycle)
    history_start: str = field(default_factory=lambda: os.getenv("HISTORY_START", "1999-03-10"))

    # Monte Carlo
    initial_investment: float = 50_000.0
    horizon_years: int = 5
    steps_per_year: int = 252
    n_paths: int = field(default_factory=lambda: int(os.getenv("MC_PATHS", "10000")))
    weight_step: float = 0.05
    # 0 = calibrate on the full history (base case); sensitivity scenarios cover shorter windows
    calibration_years: int = field(default_factory=lambda: int(os.getenv("CALIBRATION_YEARS", "0")))
    sensitivity_windows: tuple[int, ...] = (15, 10, 5)
    n_sample_paths: int = 40
    seed: int = 42

    # A/B test: A = 60% SPY / 40% QQQ, B = 50/50 equal weight
    ab_window_days: int = 756          # 3 years of trading days
    ab_step_days: int = 21             # one window per month
    bootstrap_iterations: int = 1000
    bootstrap_block: int = 21          # ~1 month blocks preserve autocorrelation
    alpha: float = 0.05

    # Streaming
    quote_poll_seconds: int = field(default_factory=lambda: int(os.getenv("QUOTE_POLL_SECONDS", "15")))

    # Keep only the latest N runs of each model type
    keep_runs: int = 5

    @property
    def price_symbols(self) -> tuple[str, ...]:
        return self.core_symbols + tuple(s for s in self.extra_symbols if s not in self.core_symbols)

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return self.price_symbols + self.index_symbols


@lru_cache
def get_settings() -> Settings:
    return Settings()
