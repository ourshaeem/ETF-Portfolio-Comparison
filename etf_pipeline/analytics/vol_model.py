"""Custom ML model: forecast next-month (21 trading day) realised volatility.

Why: the Monte Carlo uses *historical* volatility, which lags regime changes. A
forward-looking vol forecast lets the allocator stress-test the frontier under
current market conditions (the API exposes this as the "ML volatility" scenario).

Models compared with walk-forward validation (expanding window, 21-day gap so the
21-day-ahead target never leaks into training):
  * naive_rv21    — tomorrow's vol = last month's vol (random walk benchmark)
  * har_ols       — HAR-RV (Corsi, 2009): linear model on daily/weekly/monthly vol
  * gbm_regressor — gradient-boosted trees on HAR terms + VIX, momentum, drawdown
  * har_gbm_hybrid — HAR-OLS backbone + gradient boosting on its residuals. Trees
                     alone cannot extrapolate beyond the vol levels seen in training
                     (e.g. a 2008-style spike); the linear backbone can, and the trees
                     add the non-linear VIX/momentum effects on top.
All models are fit on log-vol and mapped back, which keeps forecasts positive.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

TRADING_DAYS = 252
HORIZON = 21
HAR_FEATURES = ["log_rv_1", "log_rv_5", "log_rv_21"]
GBM_FEATURES = HAR_FEATURES + ["log_rv_63", "ret_5", "ret_21", "vix", "vix_chg_5", "drawdown_252"]
EPS = 1e-4


def build_features(log_ret: pd.Series, adj_close: pd.Series, vix: pd.Series | None) -> pd.DataFrame:
    """Feature matrix + target for one asset. Index = trade date."""
    ann = np.sqrt(TRADING_DAYS)
    f = pd.DataFrame(index=log_ret.index)
    f["rv_21"] = log_ret.rolling(21).std() * ann
    f["log_rv_1"] = np.log(log_ret.abs() * ann + EPS)
    f["log_rv_5"] = np.log(log_ret.rolling(5).std() * ann + EPS)
    f["log_rv_21"] = np.log(f["rv_21"] + EPS)
    f["log_rv_63"] = np.log(log_ret.rolling(63).std() * ann + EPS)
    f["ret_5"] = log_ret.rolling(5).sum()
    f["ret_21"] = log_ret.rolling(21).sum()
    f["drawdown_252"] = adj_close / adj_close.rolling(252, min_periods=1).max() - 1
    if vix is not None:
        v = vix.reindex(f.index).ffill() / 100
        f["vix"] = v
        f["vix_chg_5"] = v.pct_change(5)
    else:
        f["vix"] = np.nan
        f["vix_chg_5"] = np.nan
    f["target"] = forward_realized_vol(log_ret)
    return f


def forward_realized_vol(log_ret: pd.Series, horizon: int = HORIZON) -> pd.Series:
    """Annualised std of returns over days t+1..t+horizon (NaN for the last `horizon` days)."""
    return log_ret.rolling(horizon).std().shift(-horizon) * np.sqrt(TRADING_DAYS)


def _qlike(actual: np.ndarray, pred: np.ndarray) -> float:
    ratio = actual**2 / pred**2
    return float(np.mean(ratio - np.log(ratio) - 1))


def _gbm(**overrides) -> HistGradientBoostingRegressor:
    params = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40,
                  l2_regularization=1.0, random_state=42)
    return HistGradientBoostingRegressor(**{**params, **overrides})


class ColumnModel:
    """An sklearn regressor bound to the feature columns it uses."""

    def __init__(self, estimator, cols: list[str]):
        self.estimator, self.cols = estimator, cols

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "ColumnModel":
        self.estimator.fit(X[self.cols].to_numpy(), y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict(X[self.cols].to_numpy())


class HarResidualBoost:
    """HAR-OLS prediction + shallow, heavily regularised boosting on the HAR residuals."""

    def __init__(self, gbm_features: list[str] = GBM_FEATURES):
        self.har = ColumnModel(LinearRegression(), HAR_FEATURES)
        self.boost = ColumnModel(
            _gbm(max_iter=150, learning_rate=0.03, max_depth=3, min_samples_leaf=80, l2_regularization=5.0),
            gbm_features,
        )

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "HarResidualBoost":
        self.har.fit(X, y)
        self.boost.fit(X, y - self.har.predict(X))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.har.predict(X) + self.boost.predict(X)


def _models(gbm_features: list[str]) -> dict:
    return {
        "har_ols": ColumnModel(LinearRegression(), HAR_FEATURES),
        "gbm_regressor": ColumnModel(_gbm(), gbm_features),
        "har_gbm_hybrid": HarResidualBoost(gbm_features),
    }


def _score(actual: np.ndarray, pred: np.ndarray) -> dict:
    return {
        "rmse": float(np.sqrt(mean_squared_error(actual, pred))),
        "mae": float(mean_absolute_error(actual, pred)),
        "r2": float(r2_score(actual, pred)),
        "qlike": _qlike(actual, pred),
        "n_obs": int(len(actual)),
    }


def walk_forward(features: pd.DataFrame, n_splits: int = 5) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (fold metrics, out-of-sample predictions, next-month forecast per model)."""
    data = features.dropna(subset=GBM_FEATURES[:4] + ["ret_21", "drawdown_252"])
    labelled = data.dropna(subset=["target"])
    y = labelled["target"].to_numpy()
    log_y = np.log(y)
    # Drop features with no data at all (e.g. VIX unavailable) rather than failing
    gbm_features = [c for c in GBM_FEATURES if labelled[c].notna().any()]

    metric_rows, pred_frames = [], []
    pooled: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    splitter = TimeSeriesSplit(n_splits=n_splits, gap=HORIZON)

    for fold, (tr, te) in enumerate(splitter.split(labelled), start=1):
        test_idx = labelled.index[te]
        preds = {"naive_rv21": labelled["rv_21"].to_numpy()[te]}
        train, test = labelled.iloc[tr], labelled.iloc[te]
        for name, model in _models(gbm_features).items():
            preds[name] = np.exp(model.fit(train, log_y[tr]).predict(test))
        for name, p in preds.items():
            metric_rows.append({"model_name": name, "fold": fold, **_score(y[te], p)})
            pooled.setdefault(name, []).append((y[te], p))
            pred_frames.append(pd.DataFrame({"model_name": name, "trade_date": test_idx, "predicted_vol": p}))

    for name, parts in pooled.items():
        actual = np.concatenate([a for a, _ in parts])
        pred = np.concatenate([p for _, p in parts])
        metric_rows.append({"model_name": name, "fold": 0, **_score(actual, pred)})

    # Refit on all labelled data, forecast from the latest (unlabelled) row
    latest = data.iloc[[-1]]
    forecasts = {"naive_rv21": float(latest["rv_21"].iloc[0])}
    for name, model in _models(gbm_features).items():
        forecasts[name] = float(np.exp(model.fit(labelled, log_y).predict(latest)[0]))

    return pd.DataFrame(metric_rows), pd.concat(pred_frames, ignore_index=True), forecasts
