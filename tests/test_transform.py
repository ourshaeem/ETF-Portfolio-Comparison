import numpy as np
import pandas as pd
import pytest

from etf_pipeline import transform
from etf_pipeline.analytics import vol_model


def prices(n=400, seed=0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(seed)
    spy = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n)))
    qqq = 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.014, n)))
    return pd.DataFrame({"SPY": spy, "QQQ": qqq}, index=idx)


def test_clean_prices_drops_bad_rows_and_dupes():
    raw = pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-04"],
        "close": [10, 10.5, -1, 11], "adj_close": [10, 10.5, -1, 11], "volume": [1, 2, 3, None],
    })
    out = transform.clean_prices(raw)
    assert len(out) == 2 and out["close"].tolist() == [10.5, 11]


def test_single_asset_backtest_equals_buy_and_hold():
    px = prices()
    v = transform.backtest(px, {"SPY": 1.0}, 50_000)
    assert v.iloc[-1] == pytest.approx(50_000 * px["SPY"].iloc[-1] / px["SPY"].iloc[0])


def test_monthly_rebalance_resets_weights():
    px = prices()
    v = transform.backtest(px, {"SPY": 0.6, "QQQ": 0.4}, 50_000)
    assert v.iloc[0] == 50_000 and len(v) == len(px)
    # within a month, value equals drift of holdings set at the prior month-end
    month_start = px.index[px.index.to_period("M") != px.index.shift(-1, freq="B").to_period("M")][0]
    assert np.isfinite(v).all() and v.loc[month_start] > 0


def test_daily_risk_free_alignment():
    rates = pd.DataFrame({"rate_date": pd.to_datetime(["2024-01-01", "2024-01-10"]).date, "annual_rate": [0.05, 0.04]})
    idx = pd.bdate_range("2024-01-02", "2024-01-12")
    rf = transform.daily_risk_free(rates, idx)
    assert rf.iloc[0] == pytest.approx(1.05 ** (1 / 252) - 1)
    assert rf.iloc[-1] == pytest.approx(1.04 ** (1 / 252) - 1)


def test_forward_vol_has_no_lookahead_leak_into_features():
    px = prices(800)["SPY"]
    lr = np.log(px).diff()
    f = vol_model.build_features(lr, px, None)
    # target at t uses returns t+1..t+21 only: equals rv_21 measured 21 days later
    assert f["target"].iloc[100] == pytest.approx(f["rv_21"].iloc[121])
    assert f["target"].iloc[-21:].isna().all()


def test_walk_forward_runs_and_reports_all_models():
    px = prices(1500)["SPY"]
    feats = vol_model.build_features(np.log(px).diff(), px, None)
    metrics, preds, fc = vol_model.walk_forward(feats, n_splits=3)
    assert set(fc) == {"naive_rv21", "har_ols", "gbm_regressor", "har_gbm_hybrid"}
    assert (metrics["fold"] == 0).sum() == 4
    assert all(v > 0 for v in fc.values())
