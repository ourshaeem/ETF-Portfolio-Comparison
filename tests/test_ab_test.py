import numpy as np
import pandas as pd
import pytest

from etf_pipeline.analytics import ab_test


def paired_returns(n=3000, edge=0.0, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-01", periods=n)
    common = rng.normal(0.0004, 0.01, n)
    a = pd.Series(common + rng.normal(edge, 0.002, n), index=idx)
    b = pd.Series(common + rng.normal(0.0, 0.002, n), index=idx)
    return a, b, pd.Series(0.0001, index=idx)


def test_block_bootstrap_indices_shape_and_range():
    idx = ab_test.block_bootstrap_indices(100, 50, 10, np.random.default_rng(0))
    assert idx.shape == (50, 100) and idx.min() >= 0 and idx.max() < 100


def test_rolling_windows_count_and_order():
    a, b, rf = paired_returns(n=1000)
    cfg = ab_test.ABConfig(window_days=252, step_days=21)
    w = ab_test.rolling_windows(a, b, rf, cfg)
    assert len(w) == len(range(999, 250, -21))
    assert w["window_end"].is_monotonic_increasing and w["window_end"].iloc[-1] == a.index[-1]


def test_no_false_positive_under_null():
    a, b, rf = paired_returns(edge=0.0, seed=7)
    _, res, boot = ab_test.run_ab_test(a, b, rf, ab_test.ABConfig(window_days=252, bootstrap_iterations=500))
    r = res.set_index("test_name")
    assert len(boot) == 500
    assert not r.loc["bootstrap_sharpe_diff", "significant"]
    assert r.loc["bootstrap_sharpe_diff", "ci_low"] < 0 < r.loc["bootstrap_sharpe_diff", "ci_high"]


def test_detects_a_real_edge():
    a, b, rf = paired_returns(edge=0.0004, seed=7)   # ~10%/yr extra for A
    _, res, _ = ab_test.run_ab_test(a, b, rf, ab_test.ABConfig(window_days=252, bootstrap_iterations=500))
    r = res.set_index("test_name")
    assert r.loc["paired_t_daily_returns", "significant"]
    assert r.loc["bootstrap_sharpe_diff", "estimate"] > 0 and r.loc["bootstrap_sharpe_diff", "significant"]


def test_hac_is_more_conservative_than_naive_for_overlapping_windows():
    a, b, rf = paired_returns(edge=0.00005, seed=3)
    _, res, _ = ab_test.run_ab_test(a, b, rf, ab_test.ABConfig(window_days=504, step_days=21, bootstrap_iterations=200))
    r = res.set_index("test_name")
    assert r.loc["hac_t_rolling_sharpe", "p_value"] >= r.loc["paired_t_rolling_sharpe", "p_value"]
