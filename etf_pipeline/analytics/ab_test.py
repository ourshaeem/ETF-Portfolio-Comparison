"""A/B hypothesis test: Strategy A (60% SPY / 40% QQQ) vs Strategy B (50/50 equal weight).

H0: the two strategies have the same risk-adjusted return (Sharpe A - Sharpe B = 0).

Tests reported
--------------
1. Paired t-test on rolling 3-year Sharpe ratios (as specified). Windows overlap by
   35 of 36 months, so observations are heavily autocorrelated and the naive t-test
   overstates significance. We therefore also report:
2. The same comparison with a Newey-West (HAC) standard error.
3. Paired t-test on daily returns (difference in mean return).
4. 1,000-iteration circular *block* bootstrap of paired daily returns for the
   full-sample Sharpe and return differences. Resampling A and B on the same days
   preserves their correlation; blocks preserve volatility clustering.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from . import metrics

TRADING_DAYS = 252


@dataclass(frozen=True)
class ABConfig:
    window_days: int = 756
    step_days: int = 21
    bootstrap_iterations: int = 1000
    block_length: int = 21
    alpha: float = 0.05
    seed: int = 42


def rolling_windows(ret_a: pd.Series, ret_b: pd.Series, rf_daily: pd.Series, cfg: ABConfig) -> pd.DataFrame:
    """Metrics for each trailing window, stepping back monthly from the latest date."""
    n = len(ret_a)
    ends = list(range(n - 1, cfg.window_days - 2, -cfg.step_days))[::-1]
    rows = []
    for end in ends:
        sl = slice(end - cfg.window_days + 1, end + 1)
        a, b, rf = ret_a.iloc[sl], ret_b.iloc[sl], rf_daily.iloc[sl]
        rows.append(
            {
                "window_start": a.index[0],
                "window_end": a.index[-1],
                "return_a": metrics.annualized_return(a),
                "return_b": metrics.annualized_return(b),
                "vol_a": metrics.annualized_vol(a),
                "vol_b": metrics.annualized_vol(b),
                "sharpe_a": metrics.sharpe_ratio(a, rf),
                "sharpe_b": metrics.sharpe_ratio(b, rf),
                "mdd_a": metrics.max_drawdown_from_returns(a),
                "mdd_b": metrics.max_drawdown_from_returns(b),
                "tracking_error": metrics.tracking_error(a, b),
            }
        )
    return pd.DataFrame(rows)


def newey_west_t(diff: np.ndarray, lags: int) -> tuple[float, float]:
    """t-statistic for mean(diff) = 0 with a Bartlett-kernel HAC standard error."""
    d = np.asarray(diff, dtype=float)
    n = d.size
    e = d - d.mean()
    lrv = e @ e / n
    for lag in range(1, min(lags, n - 1) + 1):
        w = 1 - lag / (lags + 1)
        lrv += 2 * w * (e[lag:] @ e[:-lag]) / n
    se = np.sqrt(lrv / n)
    t = d.mean() / se
    # Effective sample size is far smaller than n for overlapping windows; use n/lags dof
    dof = max(n // max(lags, 1) - 1, 1)
    return float(t), float(2 * stats.t.sf(abs(t), dof))


def block_bootstrap_indices(n: int, n_iter: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap: (n_iter, n) matrix of resampled row indices."""
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(n_iter, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)) % n
    return idx.reshape(n_iter, -1)[:, :n]


def _sharpe_rows(r: np.ndarray, rf: np.ndarray) -> np.ndarray:
    ex = r - rf
    return ex.mean(axis=1) / ex.std(axis=1, ddof=1) * np.sqrt(TRADING_DAYS)


def _ann_return_rows(r: np.ndarray) -> np.ndarray:
    return np.exp(np.log1p(r).sum(axis=1) * TRADING_DAYS / r.shape[1]) - 1


def bootstrap(ret_a: pd.Series, ret_b: pd.Series, rf_daily: pd.Series, cfg: ABConfig) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.seed)
    a, b, rf = ret_a.to_numpy(), ret_b.to_numpy(), rf_daily.to_numpy()
    idx = block_bootstrap_indices(len(a), cfg.bootstrap_iterations, cfg.block_length, rng)
    ra, rb, rr = a[idx], b[idx], rf[idx]
    return pd.DataFrame(
        {
            "iteration": np.arange(cfg.bootstrap_iterations),
            "sharpe_diff": _sharpe_rows(ra, rr) - _sharpe_rows(rb, rr),
            "return_diff": _ann_return_rows(ra) - _ann_return_rows(rb),
        }
    )


def _bootstrap_row(name: str, metric: str, observed: float, draws: np.ndarray, alpha: float, note: str) -> dict:
    lo, hi = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    # p-value under H0 by centring the bootstrap distribution on zero
    p = float(np.mean(np.abs(draws - draws.mean()) >= abs(observed)))
    return {
        "test_name": name, "metric": metric, "estimate": observed, "statistic": None,
        "p_value": p, "ci_low": float(lo), "ci_high": float(hi), "n_obs": len(draws),
        "significant": p < alpha, "notes": note,
    }


def run_ab_test(
    ret_a: pd.Series, ret_b: pd.Series, rf_daily: pd.Series, cfg: ABConfig = ABConfig()
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (rolling_windows, test_results, bootstrap_draws)."""
    df = pd.concat({"a": ret_a, "b": ret_b, "rf": rf_daily}, axis=1).dropna()
    ret_a, ret_b, rf = df["a"], df["b"], df["rf"]

    rolling = rolling_windows(ret_a, ret_b, rf, cfg)
    diff = (rolling["sharpe_a"] - rolling["sharpe_b"]).to_numpy()
    lags = cfg.window_days // cfg.step_days
    results = []

    t, p = stats.ttest_rel(rolling["sharpe_a"], rolling["sharpe_b"])
    ci = stats.t.interval(1 - cfg.alpha, len(diff) - 1, loc=diff.mean(), scale=stats.sem(diff))
    results.append({
        "test_name": "paired_t_rolling_sharpe", "metric": "rolling 3y Sharpe (A - B)",
        "estimate": float(diff.mean()), "statistic": float(t), "p_value": float(p),
        "ci_low": float(ci[0]), "ci_high": float(ci[1]), "n_obs": len(diff), "significant": p < cfg.alpha,
        "notes": "Naive paired t-test as specified; overlapping windows make p-values too small.",
    })

    t_hac, p_hac = newey_west_t(diff, lags)
    results.append({
        "test_name": "hac_t_rolling_sharpe", "metric": "rolling 3y Sharpe (A - B)",
        "estimate": float(diff.mean()), "statistic": t_hac, "p_value": p_hac,
        "ci_low": None, "ci_high": None, "n_obs": len(diff), "significant": p_hac < cfg.alpha,
        "notes": f"Newey-West HAC standard error ({lags} lags) — corrects for window overlap.",
    })

    t_d, p_d = stats.ttest_rel(ret_a, ret_b)
    d = (ret_a - ret_b).to_numpy()
    results.append({
        "test_name": "paired_t_daily_returns", "metric": "mean daily return (A - B), annualised",
        "estimate": float(d.mean() * TRADING_DAYS), "statistic": float(t_d), "p_value": float(p_d),
        "ci_low": None, "ci_high": None, "n_obs": len(d), "significant": p_d < cfg.alpha,
        "notes": "Paired t-test on same-day returns.",
    })

    boot = bootstrap(ret_a, ret_b, rf, cfg)
    sharpe_obs = metrics.sharpe_ratio(ret_a, rf) - metrics.sharpe_ratio(ret_b, rf)
    return_obs = metrics.annualized_return(ret_a) - metrics.annualized_return(ret_b)
    note = f"{cfg.bootstrap_iterations}-iteration circular block bootstrap ({cfg.block_length}-day blocks)."
    results.append(_bootstrap_row("bootstrap_sharpe_diff", "full-sample Sharpe (A - B)",
                                  sharpe_obs, boot["sharpe_diff"].to_numpy(), cfg.alpha, note))
    results.append(_bootstrap_row("bootstrap_return_diff", "annualised return (A - B)",
                                  return_obs, boot["return_diff"].to_numpy(), cfg.alpha, note))

    results.append({
        "test_name": "rolling_win_rate", "metric": "share of 3y windows where Sharpe A > B",
        "estimate": float((diff > 0).mean()), "statistic": None, "p_value": None,
        "ci_low": None, "ci_high": None, "n_obs": len(diff), "significant": None,
        "notes": "Descriptive.",
    })
    results.append({
        "test_name": "tracking_error", "metric": "annualised tracking error of A vs B",
        "estimate": metrics.tracking_error(ret_a, ret_b), "statistic": None, "p_value": None,
        "ci_low": None, "ci_high": None, "n_obs": len(d), "significant": None,
        "notes": "Descriptive: how far apart the two strategies' returns drift.",
    })
    return rolling, pd.DataFrame(results), boot
