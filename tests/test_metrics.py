import numpy as np
import pandas as pd
import pytest

from etf_pipeline.analytics import metrics


def test_annualized_return_constant_growth():
    daily = (1.10) ** (1 / 252) - 1          # exactly 10% a year
    assert metrics.annualized_return(np.full(252 * 3, daily)) == pytest.approx(0.10, rel=1e-9)


def test_sharpe_matches_definition():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0005, 0.01, 5000)
    rf = 0.0001
    expected = (r - rf).mean() / (r - rf).std(ddof=1) * np.sqrt(252)
    assert metrics.sharpe_ratio(r, rf) == pytest.approx(expected)


def test_sortino_ignores_upside_volatility():
    calm = np.array([0.01, -0.01] * 100)
    upside = np.array([0.03, -0.01] * 100)   # same downside, bigger upside
    assert metrics.sortino_ratio(upside) > metrics.sortino_ratio(calm)


def test_max_drawdown_known_path():
    values = [100, 120, 90, 110, 60, 130]
    assert metrics.max_drawdown(values) == pytest.approx(0.5)   # 120 -> 60


def test_var_and_cvar_on_uniform_losses():
    r = np.linspace(-0.10, 0.10, 2001)
    assert metrics.historical_var(r, 0.95) == pytest.approx(0.09, abs=1e-3)
    assert metrics.historical_cvar(r, 0.95) == pytest.approx(0.095, abs=1e-3)


def test_summarize_keys_and_types():
    idx = pd.bdate_range("2020-01-01", periods=600)
    r = pd.Series(np.random.default_rng(1).normal(0.0004, 0.01, 600), index=idx)
    out = metrics.summarize(r, pd.Series(0.0001, index=idx))
    assert set(out) == {"total_return", "cagr", "vol_annual", "sharpe", "sortino", "max_drawdown", "var95_daily", "cvar95_daily"}
    assert all(np.isfinite(v) for v in out.values())
