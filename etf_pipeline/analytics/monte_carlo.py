"""Monte Carlo engine: correlated geometric Brownian motion for SPY & QQQ.

Model
-----
Each asset follows dS/S = mu dt + sigma dW with correlated Brownian motions. Daily log
returns are therefore multivariate normal with mean (mu - sigma^2/2)/252 and covariance
Sigma/252, both estimated from history. We simulate the *asset* returns once and reuse
the same shocks for every allocation (common random numbers), so differences across
the 0%..100% QQQ frontier reflect the allocation, not simulation noise.

Portfolios are rebalanced daily to constant weights, so a w% QQQ portfolio's daily
return is (1-w) * r_SPY + w * r_QQQ.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252
FAN_PERCENTILES = (5, 25, 50, 75, 95)


@dataclass(frozen=True)
class GBMCalibration:
    symbols: tuple[str, ...]           # e.g. ('SPY', 'QQQ') — QQQ must be last
    mean_log_daily: np.ndarray         # (k,)
    cov_log_daily: np.ndarray          # (k, k)
    start: date
    end: date

    @property
    def sigma_annual(self) -> np.ndarray:
        return np.sqrt(np.diag(self.cov_log_daily) * TRADING_DAYS)

    @property
    def mu_annual(self) -> np.ndarray:
        """Arithmetic drift: E[log return] + sigma^2 / 2, annualised."""
        return (self.mean_log_daily + 0.5 * np.diag(self.cov_log_daily)) * TRADING_DAYS

    @property
    def corr(self) -> np.ndarray:
        sd = np.sqrt(np.diag(self.cov_log_daily))
        return self.cov_log_daily / np.outer(sd, sd)

    def with_mu(self, mu_annual: np.ndarray) -> "GBMCalibration":
        """Swap in new arithmetic drifts, keeping the covariance."""
        mean_log = np.asarray(mu_annual) / TRADING_DAYS - 0.5 * np.diag(self.cov_log_daily)
        return GBMCalibration(self.symbols, mean_log, self.cov_log_daily, self.start, self.end)

    def equal_sharpe(self, rf_annual: float) -> "GBMCalibration":
        """'No-skill' prior: give every asset the average historical Sharpe ratio.

        Expected returns are the noisiest input to any allocation model (the standard
        error of a 20-year mean is ~sigma/sqrt(20) ~ 5%/yr). Under this scenario QQQ
        earns more only in proportion to its extra risk, so the optimiser can no longer
        simply chase QQQ's past outperformance.
        """
        sharpe = (self.mu_annual - rf_annual) / self.sigma_annual
        return self.with_mu(rf_annual + sharpe.mean() * self.sigma_annual)

    def with_sigma(self, sigma_annual: np.ndarray) -> "GBMCalibration":
        """Swap in new volatilities (e.g. ML forecasts), keeping correlations and arithmetic drift."""
        sigma_d = np.asarray(sigma_annual) / np.sqrt(TRADING_DAYS)
        cov = self.corr * np.outer(sigma_d, sigma_d)
        mu_d = self.mu_annual / TRADING_DAYS
        return GBMCalibration(self.symbols, mu_d - 0.5 * sigma_d**2, cov, self.start, self.end)


def calibrate(log_returns: pd.DataFrame) -> GBMCalibration:
    """Estimate GBM parameters from a (dates x symbols) frame of daily log returns."""
    lr = log_returns.dropna()
    return GBMCalibration(
        symbols=tuple(lr.columns),
        mean_log_daily=lr.mean().to_numpy(),
        cov_log_daily=lr.cov().to_numpy(),
        start=pd.Timestamp(lr.index.min()).date(),
        end=pd.Timestamp(lr.index.max()).date(),
    )


def simulate_asset_returns(cal: GBMCalibration, n_paths: int, n_steps: int, seed: int) -> np.ndarray:
    """Daily *simple* returns, shape (n_steps, n_paths, k), float32 to keep memory ~100 MB."""
    rng = np.random.default_rng(seed)
    chol = np.linalg.cholesky(cal.cov_log_daily).astype(np.float32)
    z = rng.standard_normal((n_steps, n_paths, len(cal.symbols)), dtype=np.float32)
    log_r = z @ chol.T + cal.mean_log_daily.astype(np.float32)
    return np.expm1(log_r, out=log_r)


@dataclass
class AllocationResult:
    qqq_weight: float
    summary: dict
    fan: pd.DataFrame
    terminal: np.ndarray
    value_1y: np.ndarray
    max_drawdown: np.ndarray
    sample_paths: pd.DataFrame


def simulate_allocation(
    asset_returns: np.ndarray,
    cal: GBMCalibration,
    qqq_weight: float,
    initial: float,
    rf_annual: float,
    n_sample_paths: int = 40,
    sample_stride: int = 5,
    detail: bool = True,
    equity_fraction: float = 1.0,
) -> AllocationResult:
    """Simulate one allocation.

    equity_fraction < 1 holds the rest in T-bills earning rf (a cash sleeve).
    detail=False skips fan percentiles and sample paths (used where only the summary
    is needed: sensitivity scenarios and the cash-sleeve search).
    """
    n_steps = asset_returns.shape[0]
    w = np.array([1.0 - qqq_weight, qqq_weight], dtype=np.float32)
    port_r = asset_returns @ w                                   # (n_steps, n_paths)
    if equity_fraction < 1.0:
        port_r = equity_fraction * port_r + np.float32((1 - equity_fraction) * rf_annual / TRADING_DAYS)

    values = np.empty((n_steps + 1, port_r.shape[1]), dtype=np.float32)
    values[0] = initial
    np.cumprod(1.0 + port_r, axis=0, out=values[1:])
    values[1:] *= initial

    # Per-path realised Sharpe / Sortino over the horizon
    rf_d = rf_annual / TRADING_DAYS
    ex = port_r.astype(np.float64) - rf_d
    mean_ex = ex.mean(axis=0)
    sharpe_paths = mean_ex / ex.std(axis=0, ddof=1) * np.sqrt(TRADING_DAYS)
    downside = np.sqrt(np.mean(np.minimum(ex, 0.0) ** 2, axis=0))
    sortino_paths = mean_ex / downside * np.sqrt(TRADING_DAYS)
    del ex

    peak = np.maximum.accumulate(values, axis=0)
    mdd = -(values / peak - 1.0).min(axis=0).astype(np.float64)
    del peak

    terminal = values[-1].astype(np.float64)
    one_year = min(TRADING_DAYS, n_steps)
    v1y = values[one_year].astype(np.float64)
    years = n_steps / TRADING_DAYS

    q05_1y = np.quantile(v1y, 0.05)
    var95_1y = initial - q05_1y
    tail_1y = v1y[v1y <= q05_1y]

    # Ex-ante (analytical) portfolio moments
    w64 = w.astype(np.float64)
    cov_annual = cal.cov_log_daily * TRADING_DAYS
    exp_ret = float(equity_fraction * (w64 @ cal.mu_annual) + (1 - equity_fraction) * rf_annual)
    vol = float(equity_fraction * np.sqrt(w64 @ cov_annual @ w64))

    summary = {
        "qqq_weight": round(qqq_weight, 3),
        "exp_return_annual": exp_ret,
        "vol_annual": vol,
        "sharpe_ex_ante": (exp_ret - rf_annual) / vol if vol > 0 else np.nan,
        "sharpe_median": float(np.median(sharpe_paths)),
        "sortino_median": float(np.median(sortino_paths)),
        "cagr_median": float(np.median((terminal / initial) ** (1 / years) - 1)),
        "terminal_mean": float(terminal.mean()),
        "terminal_p05": float(np.quantile(terminal, 0.05)),
        "terminal_p50": float(np.median(terminal)),
        "terminal_p95": float(np.quantile(terminal, 0.95)),
        "var95_1y": float(var95_1y),
        "var95_1y_pct": float(var95_1y / initial * 100),
        "cvar95_1y": float(initial - tail_1y.mean()),
        "var95_horizon": float(initial - np.quantile(terminal, 0.05)),
        "mdd_median": float(np.median(mdd)),
        "mdd_p95": float(np.quantile(mdd, 0.95)),
        "prob_loss_horizon": float((terminal < initial).mean()),
    }

    if not detail:
        return AllocationResult(qqq_weight, summary, pd.DataFrame(), terminal, v1y, mdd, pd.DataFrame())

    pct = np.percentile(values, FAN_PERCENTILES, axis=1)       # (5, n_steps + 1)
    fan = pd.DataFrame(
        {
            "step": np.arange(n_steps + 1),
            **{f"p{p:02d}": pct[i] for i, p in enumerate(FAN_PERCENTILES)},
            "mean": values.mean(axis=1, dtype=np.float64),
        }
    )

    steps = np.unique(np.r_[np.arange(0, n_steps + 1, sample_stride), n_steps])
    k = min(n_sample_paths, values.shape[1])
    sample = pd.DataFrame(
        {
            "path_id": np.repeat(np.arange(k), len(steps)),
            "step": np.tile(steps, k),
            "value": values[np.ix_(steps, np.arange(k))].T.ravel().astype(np.float64),
        }
    )
    return AllocationResult(qqq_weight, summary, fan, terminal, v1y, mdd, sample)


def allocation_grid(step: float = 0.05) -> list[float]:
    n = int(round(1 / step))
    return [round(i * step, 4) for i in range(n + 1)]


def run_frontier(
    cal: GBMCalibration,
    initial: float,
    rf_annual: float,
    horizon_years: int = 5,
    n_paths: int = 10_000,
    weight_step: float = 0.05,
    seed: int = 42,
    n_sample_paths: int = 40,
    weights: list[float] | None = None,
    detail: bool = True,
    asset_returns: np.ndarray | None = None,
) -> list[AllocationResult]:
    """Simulate every allocation on the 0%..100% QQQ grid with shared random shocks."""
    if cal.symbols[-1] != "QQQ":
        raise ValueError("calibration symbols must be ordered (SPY, QQQ)")
    asset_r = (
        asset_returns if asset_returns is not None
        else simulate_asset_returns(cal, n_paths, horizon_years * TRADING_DAYS, seed)
    )
    return [
        simulate_allocation(asset_r, cal, w, initial, rf_annual, n_sample_paths, detail=detail)
        for w in (weights if weights is not None else allocation_grid(weight_step))
    ]


RECOMMENDATION_METRICS = (
    "exp_return_annual", "vol_annual", "sharpe_median", "cagr_median", "var95_1y", "var95_1y_pct",
    "mdd_p95", "terminal_p05", "terminal_p50", "terminal_p95",
)


def cash_sleeve(simulate, qqq_weight: float, max_var_pct: float, iterations: int = 12) -> tuple[float, dict]:
    """Largest equity fraction (rest in T-bills) whose 1-year 95% VaR <= max_var_pct.

    `simulate(qqq_weight, equity_fraction) -> summary dict`. VaR rises monotonically
    with equity exposure, so a bisection converges quickly.
    """
    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        mid = (lo + hi) / 2
        if simulate(qqq_weight, mid)["var95_1y_pct"] <= max_var_pct:
            lo = mid
        else:
            hi = mid
    frac = float(np.floor(lo * 100) / 100)  # round down to whole percent: stays within tolerance
    return frac, simulate(qqq_weight, frac)


def recommend(summary: pd.DataFrame, max_var_pct: float, simulate=None) -> dict:
    """Max-Sharpe SPY/QQQ mix subject to 1-year 95% VaR <= max_var_pct of capital.

    If no all-equity mix fits the tolerance and `simulate` is given, apply Tobin's
    separation theorem: hold the max-Sharpe (tangency) mix, and put just enough in
    T-bills to bring VaR within tolerance. Cash is riskless, so this keeps the best
    available risk-adjusted return instead of settling for a lower-Sharpe mix.
    """
    feasible = summary[summary["var95_1y_pct"] <= max_var_pct]
    if not feasible.empty:
        best = feasible.sort_values(["sharpe_median", "var95_1y_pct"], ascending=[False, True]).iloc[0]
        return {
            "recommended_qqq_weight": float(best["qqq_weight"]),
            "equity_fraction": 1.0,
            "within_tolerance": True,
            "rationale": (
                f"{best['qqq_weight']:.0%} QQQ / {1 - best['qqq_weight']:.0%} SPY has the highest median "
                f"5-year Sharpe ({best['sharpe_median']:.2f}) among mixes with 1-year 95% VaR <= "
                f"{max_var_pct:.0f}% (this mix: {best['var95_1y_pct']:.1f}%, i.e. ${best['var95_1y']:,.0f})."
            ),
            **{k: float(best[k]) for k in RECOMMENDATION_METRICS},
        }

    tangency = summary.loc[summary["sharpe_median"].idxmax()]
    w = float(tangency["qqq_weight"])
    lowest = summary["var95_1y_pct"].min()
    if simulate is None:
        safest = summary.loc[summary["var95_1y_pct"].idxmin()]
        return {
            "recommended_qqq_weight": float(safest["qqq_weight"]), "equity_fraction": 1.0,
            "within_tolerance": False,
            "rationale": f"No SPY/QQQ mix keeps 1-year 95% VaR within {max_var_pct:.0f}% (lowest: {lowest:.1f}%).",
            **{k: float(safest[k]) for k in RECOMMENDATION_METRICS},
        }
    frac, blended = cash_sleeve(simulate, w, max_var_pct)
    return {
        "recommended_qqq_weight": w,
        "equity_fraction": frac,
        "within_tolerance": True,
        "rationale": (
            f"No all-equity mix fits a {max_var_pct:.0f}% VaR limit (lowest: {lowest:.1f}%). Hold "
            f"{frac:.0%} in the max-Sharpe mix ({w:.0%} QQQ / {1 - w:.0%} SPY) and {1 - frac:.0%} in "
            f"T-bills: 1-year 95% VaR {blended['var95_1y_pct']:.1f}% (${blended['var95_1y']:,.0f}), "
            f"same Sharpe as the mix."
        ),
        **{k: float(blended[k]) for k in RECOMMENDATION_METRICS},
    }
