import numpy as np
import pandas as pd
import pytest

from etf_pipeline.analytics import monte_carlo as mc


def make_cal(mu=(0.08, 0.12), sigma=(0.16, 0.24), rho=0.85):
    sd = np.array(sigma) / np.sqrt(252)
    cov = np.array([[1, rho], [rho, 1]]) * np.outer(sd, sd)
    mean_log = np.array(mu) / 252 - 0.5 * np.diag(cov)
    return mc.GBMCalibration(("SPY", "QQQ"), mean_log, cov, pd.Timestamp("2000-01-01").date(), pd.Timestamp("2020-01-01").date())


def test_calibrate_recovers_parameters():
    cal = make_cal()
    # ~8,000 "years" of days: drift is the hardest parameter to estimate (SE ~ sigma / sqrt(years))
    sims = np.random.default_rng(3).multivariate_normal(cal.mean_log_daily, cal.cov_log_daily, size=2_000_000)
    est = mc.calibrate(pd.DataFrame(sims, columns=["SPY", "QQQ"]))
    assert est.mu_annual == pytest.approx(cal.mu_annual, abs=0.01)
    assert est.sigma_annual == pytest.approx(cal.sigma_annual, rel=0.01)
    assert est.corr[0, 1] == pytest.approx(0.85, abs=0.01)


def test_simulated_moments_match_gbm():
    """E[V_T] = V0 * exp(mu_p * T) for a constant-weight portfolio of GBMs."""
    cal = make_cal()
    asset_r = mc.simulate_asset_returns(cal, n_paths=20_000, n_steps=252, seed=1)
    res = mc.simulate_allocation(asset_r, cal, qqq_weight=0.5, initial=50_000, rf_annual=0.03)
    mu_p = 0.5 * 0.08 + 0.5 * 0.12
    assert res.summary["terminal_mean"] == pytest.approx(50_000 * np.exp(mu_p), rel=0.01)
    assert res.summary["exp_return_annual"] == pytest.approx(mu_p)


def test_frontier_grid_and_shapes():
    cal = make_cal()
    results = mc.run_frontier(cal, 50_000, 0.03, horizon_years=1, n_paths=2_000, weight_step=0.25, seed=0, n_sample_paths=5)
    assert [r.qqq_weight for r in results] == [0.0, 0.25, 0.5, 0.75, 1.0]
    r = results[0]
    assert len(r.terminal) == 2_000 and len(r.fan) == 253
    assert (r.fan["p05"] <= r.fan["p50"]).all() and (r.fan["p50"] <= r.fan["p95"]).all()
    assert r.sample_paths["path_id"].nunique() == 5


def test_risk_rises_with_qqq_weight():
    cal = make_cal()
    results = mc.run_frontier(cal, 50_000, 0.03, horizon_years=2, n_paths=4_000, weight_step=0.5, seed=0)
    vols = [r.summary["vol_annual"] for r in results]
    mdds = [r.summary["mdd_p95"] for r in results]
    assert vols == sorted(vols) and mdds == sorted(mdds)


def test_cash_sleeve_scales_risk_not_sharpe():
    cal = make_cal()
    asset_r = mc.simulate_asset_returns(cal, 5_000, 252, seed=2)
    full = mc.simulate_allocation(asset_r, cal, 0.5, 50_000, 0.03, detail=False).summary
    half = mc.simulate_allocation(asset_r, cal, 0.5, 50_000, 0.03, detail=False, equity_fraction=0.5).summary
    assert half["vol_annual"] == pytest.approx(full["vol_annual"] / 2)
    assert half["sharpe_median"] == pytest.approx(full["sharpe_median"], abs=1e-6)
    assert half["var95_1y_pct"] < full["var95_1y_pct"]


def _summary(rows):
    return pd.DataFrame(rows, columns=["qqq_weight", "sharpe_median", "var95_1y_pct", "var95_1y"]).assign(
        **{k: 0.0 for k in mc.RECOMMENDATION_METRICS if k not in ("sharpe_median", "var95_1y_pct", "var95_1y")}
    )


def test_recommend_picks_best_feasible_sharpe():
    s = _summary([(0.0, 0.30, 15, 7500), (0.5, 0.40, 19, 9500), (1.0, 0.45, 25, 12500)])
    rec = mc.recommend(s, max_var_pct=20)
    assert rec["recommended_qqq_weight"] == 0.5 and rec["equity_fraction"] == 1.0 and rec["within_tolerance"]


def test_recommend_adds_tbills_when_nothing_fits():
    s = _summary([(0.0, 0.30, 21, 10500), (1.0, 0.45, 28, 14000)])

    def simulate(w, f):  # VaR proportional to equity exposure
        return {"var95_1y_pct": 28 * f, "var95_1y": 14000 * f, **{k: 0.0 for k in mc.RECOMMENDATION_METRICS if k not in ("var95_1y_pct", "var95_1y")}}

    rec = mc.recommend(s, max_var_pct=14, simulate=simulate)
    assert rec["recommended_qqq_weight"] == 1.0          # keeps the max-Sharpe mix
    assert rec["equity_fraction"] == pytest.approx(0.5, abs=0.01)
    assert rec["var95_1y_pct"] <= 14


def test_equal_sharpe_prior_equalises_sharpe():
    cal = make_cal().equal_sharpe(rf_annual=0.03)
    sharpe = (cal.mu_annual - 0.03) / cal.sigma_annual
    assert sharpe[0] == pytest.approx(sharpe[1])
