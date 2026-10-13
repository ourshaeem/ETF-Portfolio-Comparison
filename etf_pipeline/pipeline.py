"""End-to-end pipeline: API extract -> transform -> models -> Postgres (-> Tableau live)."""
from __future__ import annotations

import logging
import time
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text

from . import db, transform
from .analytics import ab_test, monte_carlo, vol_model
from .config import Settings, get_settings
from .extract import alphavantage_client as avc
from .extract import yfinance_client as yfc

log = logging.getLogger(__name__)


# ==================================================================== EXTRACT ==
def _load_profile(conn, prof: yfc.FundProfile, is_core: bool) -> int:
    aid = db.upsert_asset(
        conn, prof.symbol, name=prof.name, asset_class=prof.asset_class, category=prof.category,
        fund_family=prof.fund_family, expense_ratio=prof.expense_ratio,
        inception_date=prof.inception_date, total_assets=prof.total_assets, is_core=is_core,
    )
    today = date.today()
    if prof.sector_weights:
        sectors = pd.DataFrame(
            {"sector_key": list(prof.sector_weights),
             "sector_name": [k.replace("_", " ").title() for k in prof.sector_weights]}
        )
        db.upsert_df(conn, "sector", sectors, ["sector_key"], update=False)
        ids = dict(conn.execute(text("SELECT sector_key, sector_id FROM sector")).all())
        weights = pd.DataFrame(
            {"asset_id": aid, "sector_id": [ids[k] for k in prof.sector_weights],
             "as_of": today, "weight": list(prof.sector_weights.values())}
        )
        db.upsert_df(conn, "asset_sector_weight", weights, ["asset_id", "sector_id", "as_of"])
    if prof.top_holdings:
        sec = pd.DataFrame(prof.top_holdings, columns=["symbol", "name", "weight"])
        db.upsert_df(conn, "security", sec[["symbol", "name"]].drop_duplicates("symbol"), ["symbol"])
        ids = dict(conn.execute(text("SELECT symbol, security_id FROM security")).all())
        hold = pd.DataFrame(
            {"asset_id": aid, "security_id": sec["symbol"].map(ids), "as_of": today, "weight": sec["weight"]}
        ).drop_duplicates("security_id")
        db.upsert_df(conn, "asset_holding", hold, ["asset_id", "security_id", "as_of"])
    return aid


def extract_asset(symbol: str, s: Settings) -> None:
    is_index = symbol.startswith("^")
    is_core = symbol in s.core_symbols

    # --- profile: yfinance, Alpha Vantage ETF_PROFILE as fallback
    try:
        prof = yfc.fetch_profile(symbol)
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance profile failed for %s: %s", symbol, exc)
        prof = yfc.FundProfile(symbol=symbol, asset_class="INDEX" if is_index else "ETF")
        if s.alpha_vantage_key and not is_index:
            try:
                prof = avc.fetch_profile(symbol, s.alpha_vantage_key)
            except Exception as av_exc:  # noqa: BLE001
                log.warning("Alpha Vantage profile failed for %s: %s", symbol, av_exc)

    # --- prices: yfinance, Alpha Vantage TIME_SERIES_DAILY as fallback
    source, status, message = "yfinance", "ok", None
    try:
        px = yfc.fetch_prices(symbol, s.history_start)
    except Exception as exc:  # noqa: BLE001
        if not s.alpha_vantage_key or is_index:
            with db.transaction() as conn:
                db.log_ingestion(conn, f"prices:{symbol}", 0, "error", "yfinance", str(exc))
            log.error("No price source available for %s: %s", symbol, exc)
            return
        log.warning("yfinance prices failed for %s (%s) — falling back to Alpha Vantage", symbol, exc)
        px = avc.fetch_prices(symbol, s.alpha_vantage_key)
        source, status, message = "alphavantage", "fallback", str(exc)

    px = transform.clean_prices(px)
    with db.transaction() as conn:
        aid = _load_profile(conn, prof, is_core)
        rows = px[["trade_date", "open", "high", "low", "close", "adj_close", "volume"]].assign(
            asset_id=aid, source_id=db.source_id(conn, source)
        )
        # Fallback rows are unadjusted, so never let them overwrite yfinance history.
        n = db.upsert_df(conn, "price_daily", rows, ["asset_id", "trade_date"], update=(source == "yfinance"))
        divs = px.loc[px["dividend"] > 0, ["trade_date", "dividend"]].rename(
            columns={"trade_date": "ex_date", "dividend": "amount"}
        )
        db.upsert_df(conn, "dividend", divs.assign(asset_id=aid), ["asset_id", "ex_date"])
        db.log_ingestion(conn, f"prices:{symbol}", n, status, source, message)
    log.info("Loaded %s: %d rows from %s", symbol, n, source)


def extract_risk_free(s: Settings) -> None:
    """Official 3-month Treasury yield from Alpha Vantage; yfinance ^IRX as fallback."""
    rates, source, status, message = None, "alphavantage", "ok", None
    if s.alpha_vantage_key:
        try:
            rates = avc.fetch_treasury_yield(s.alpha_vantage_key)
            rates = rates[rates["rate_date"] >= pd.Timestamp(s.history_start).date()]
        except Exception as exc:  # noqa: BLE001
            log.warning("Alpha Vantage treasury yield failed (%s); using ^IRX", exc)
            message = str(exc)
    if rates is None:
        rates = yfc.fetch_risk_free(s.history_start, s.risk_free_symbol)
        source, status = "yfinance", "fallback" if s.alpha_vantage_key else "ok"
    with db.transaction() as conn:
        n = db.upsert_df(conn, "risk_free_rate", rates.assign(source_id=db.source_id(conn, source)), ["rate_date"])
        db.log_ingestion(conn, "risk_free", n, status, source, message)
    log.info("Loaded %d risk-free rows from %s", n, source)


def run_extract(s: Settings) -> None:
    for symbol in s.all_symbols:
        try:
            extract_asset(symbol, s)
        except Exception:  # noqa: BLE001 - one bad ticker must not stop the run
            log.exception("Extract failed for %s", symbol)
        time.sleep(0.5)  # be polite to Yahoo
    extract_risk_free(s)


# ================================================================== TRANSFORM ==
def load_prices() -> pd.DataFrame:
    return db.read_sql(
        """
        SELECT a.symbol, a.asset_class, p.trade_date, p.close::float AS close, p.adj_close::float AS adj_close
        FROM price_daily p JOIN asset a USING (asset_id)
        ORDER BY a.symbol, p.trade_date
        """,
        parse_dates=["trade_date"],
    )


def load_rf(index: pd.DatetimeIndex) -> pd.Series:
    rates = db.read_sql("SELECT rate_date, annual_rate::float AS annual_rate FROM risk_free_rate ORDER BY rate_date")
    return transform.daily_risk_free(rates, index)


def wide_adj_close(prices: pd.DataFrame, symbols) -> pd.DataFrame:
    sub = prices[prices["symbol"].isin(symbols)]
    return sub.pivot(index="trade_date", columns="symbol", values="adj_close")[list(symbols)].dropna()


def run_transform(s: Settings) -> None:
    prices = load_prices()
    divs = db.read_sql(
        "SELECT a.symbol, d.ex_date, d.amount::float AS amount FROM dividend d JOIN asset a USING (asset_id)",
        parse_dates=["ex_date"],
    )
    all_dates = pd.DatetimeIndex(sorted(prices["trade_date"].unique()))
    rf = load_rf(all_dates)

    with db.transaction() as conn:
        ids = db.asset_ids(conn)
        feats, scores = [], []
        for symbol, grp in prices[prices["asset_class"] == "ETF"].groupby("symbol"):
            px = grp.set_index("trade_date")[["close", "adj_close"]]
            d = divs[divs["symbol"] == symbol].set_index("ex_date")["amount"]
            f = transform.asset_features(px, d, rf, s.initial_investment)
            feats.append(f.rename_axis("trade_date").reset_index().assign(asset_id=ids[symbol]))
            for row in transform.period_stats(f["simple_return"], rf):
                scores.append({"asset_id": ids[symbol], **row})
        features = pd.concat(feats, ignore_index=True)
        features["trade_date"] = features["trade_date"].dt.date
        db.replace_table(conn, "asset_feature_daily", features)
        db.replace_table(conn, "asset_period_stat", pd.DataFrame(scores))

        wide = wide_adj_close(prices, s.core_symbols)
        daily_rows, port_scores = [], []
        for code, (name, desc, weights) in transform.PORTFOLIOS.items():
            pid = conn.execute(
                text(
                    "INSERT INTO portfolio (code, name, description) VALUES (:c, :n, :d) "
                    "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description "
                    "RETURNING portfolio_id"
                ),
                {"c": code, "n": name, "d": desc},
            ).scalar_one()
            db.upsert_df(
                conn, "portfolio_weight",
                pd.DataFrame({"portfolio_id": pid, "asset_id": [ids[k] for k in weights], "weight": list(weights.values())}),
                ["portfolio_id", "asset_id"],
            )
            frame = transform.portfolio_daily_frame(transform.backtest(wide, weights, s.initial_investment))
            daily_rows.append(frame.rename_axis("trade_date").reset_index().assign(portfolio_id=pid))
            for row in transform.period_stats(frame["daily_return"], rf):
                port_scores.append({"portfolio_id": pid, **row})
        pdaily = pd.concat(daily_rows, ignore_index=True)
        pdaily["trade_date"] = pdaily["trade_date"].dt.date
        db.replace_table(conn, "portfolio_daily", pdaily)
        db.replace_table(conn, "portfolio_period_stat", pd.DataFrame(port_scores))
    log.info("Transform complete: %d feature rows, %d portfolio rows", len(features), len(pdaily))


# ================================================================ MONTE CARLO ==
def current_risk_free() -> float:
    return float(db.read_sql("SELECT annual_rate::float AS r FROM risk_free_rate ORDER BY rate_date DESC LIMIT 1")["r"].iloc[0])


def calibration_returns(wide: pd.DataFrame, years: int) -> pd.DataFrame:
    """Daily log returns over the trailing `years` (0 = full history)."""
    if years:
        wide = wide[wide.index > wide.index.max() - pd.DateOffset(years=years)]
    return np.log(wide).diff().dropna()


def latest_ml_vol() -> tuple[str, dict[str, float]] | None:
    """Forecast vols from the ML model with the best pooled out-of-sample QLIKE."""
    df = db.read_sql(
        """SELECT a.symbol, f.model_name, f.forecast_vol, m.qlike
           FROM ml_vol_forecast f
           JOIN asset a USING (asset_id)
           JOIN ml_model_metric m ON m.run_id = f.run_id AND m.asset_id = f.asset_id
                                 AND m.model_name = f.model_name AND m.fold = 0
           WHERE f.run_id = latest_run_id('ml_vol')"""
    )
    if df.empty:
        return None
    best = df.groupby("model_name")["qlike"].mean().idxmin()
    return best, df[df["model_name"] == best].set_index("symbol")["forecast_vol"].to_dict()


def build_scenarios(s: Settings, wide: pd.DataFrame, base: monte_carlo.GBMCalibration, rf: float) -> list[tuple]:
    """(code, label, description, calibration) for each sensitivity scenario."""
    base_label = "Full history" if not s.calibration_years else f"Last {s.calibration_years} years"
    out = [("base", f"{base_label} (base case)",
            f"Historical drift & covariance, {base.start:%b %Y} - {base.end:%b %Y}", base)]
    for years in s.sensitivity_windows:
        if years == s.calibration_years:
            continue
        cal = monte_carlo.calibrate(calibration_returns(wide, years))
        out.append((f"{years}y", f"Last {years} years", f"Historical drift & covariance since {cal.start:%b %Y}", cal))
    out.append(("equal_sharpe", "Equal-Sharpe prior",
                "Base covariance; both ETFs get the same Sharpe ratio (no assumed QQQ edge)", base.equal_sharpe(rf)))
    ml = latest_ml_vol()
    if ml:
        model, vols = ml
        out.append(("ml_vol", "Current vol regime (ML)",
                    f"Base drift & correlation; volatility from the {model} next-month forecast",
                    base.with_sigma(np.array([vols["SPY"], vols["QQQ"]]))))
    return out


def run_monte_carlo(s: Settings) -> int:
    run_id = db.start_run("monte_carlo")
    try:
        wide = wide_adj_close(load_prices(), s.core_symbols)
        cal = monte_carlo.calibrate(calibration_returns(wide, s.calibration_years))
        rf = current_risk_free()
        t0 = time.monotonic()
        asset_r = monte_carlo.simulate_asset_returns(cal, s.n_paths, s.horizon_years * s.steps_per_year, s.seed)
        results = monte_carlo.run_frontier(
            cal, s.initial_investment, rf, s.horizon_years, s.n_paths, s.weight_step, s.seed, s.n_sample_paths,
            asset_returns=asset_r,
        )
        log.info("Simulated %d allocations x %d paths in %.1fs", len(results), s.n_paths, time.monotonic() - t0)

        summary = pd.DataFrame([r.summary for r in results])
        fan = pd.concat([r.fan.assign(qqq_weight=r.qqq_weight) for r in results], ignore_index=True)
        terminal = pd.concat(
            [
                pd.DataFrame({"qqq_weight": r.qqq_weight, "path_id": np.arange(len(r.terminal)),
                              "terminal_value": r.terminal, "value_1y": r.value_1y, "max_drawdown": r.max_drawdown})
                for r in results
            ],
            ignore_index=True,
        )
        samples = pd.concat([r.sample_paths.assign(qqq_weight=r.qqq_weight) for r in results], ignore_index=True)

        scenario_rows, scenario_frontiers = [], []
        for code, label, desc, sc_cal in build_scenarios(s, wide, cal, rf):
            sc_summary = summary if code == "base" else pd.DataFrame([
                r.summary for r in monte_carlo.run_frontier(
                    sc_cal, s.initial_investment, rf, s.horizon_years, s.n_paths, s.weight_step, s.seed, detail=False)
            ])
            best_w = float(sc_summary.loc[sc_summary["sharpe_median"].idxmax(), "qqq_weight"])
            scenario_rows.append({
                "scenario_code": code, "label": label, "description": desc,
                "calibration_start": sc_cal.start, "calibration_end": sc_cal.end,
                "spy_mu": sc_cal.mu_annual[0], "spy_sigma": sc_cal.sigma_annual[0],
                "qqq_mu": sc_cal.mu_annual[1], "qqq_sigma": sc_cal.sigma_annual[1],
                "correlation": sc_cal.corr[0, 1], "max_sharpe_weight": best_w,
            })
            scenario_frontiers.append(sc_summary[[
                "qqq_weight", "exp_return_annual", "vol_annual", "sharpe_median", "cagr_median",
                "var95_1y_pct", "mdd_p95", "terminal_p50"]].assign(scenario_code=code))
            log.info("Scenario %-13s max-Sharpe at %3.0f%% QQQ", code, best_w * 100)

        with db.transaction() as conn:
            ids = db.asset_ids(conn)
            conn.execute(
                text(
                    "INSERT INTO mc_run_config VALUES (:r, :n, :h, :spy, :init, :cs, :ce, :rf, :ws, :seed)"
                ),
                {"r": run_id, "n": s.n_paths, "h": s.horizon_years, "spy": s.steps_per_year,
                 "init": s.initial_investment, "cs": cal.start, "ce": cal.end, "rf": rf,
                 "ws": s.weight_step, "seed": s.seed},
            )
            db.copy_df(conn, "mc_asset_param", pd.DataFrame(
                {"run_id": run_id, "asset_id": [ids[x] for x in cal.symbols],
                 "mu_annual": cal.mu_annual, "sigma_annual": cal.sigma_annual}))
            db.copy_df(conn, "mc_asset_correlation", pd.DataFrame(
                [{"run_id": run_id, "asset_id_a": ids[cal.symbols[0]], "asset_id_b": ids[cal.symbols[1]],
                  "correlation": float(cal.corr[0, 1])}]))
            db.copy_df(conn, "mc_allocation_summary", summary.assign(run_id=run_id))
            db.copy_df(conn, "mc_fan", fan.assign(run_id=run_id))
            db.copy_df(conn, "mc_terminal", terminal.assign(run_id=run_id))
            db.copy_df(conn, "mc_sample_path", samples.assign(run_id=run_id))
            db.copy_df(conn, "mc_scenario", pd.DataFrame(scenario_rows).assign(run_id=run_id))
            db.copy_df(conn, "mc_scenario_frontier", pd.concat(scenario_frontiers, ignore_index=True).assign(run_id=run_id))

            def simulate(qqq_weight: float, equity_fraction: float) -> dict:
                return monte_carlo.simulate_allocation(
                    asset_r, cal, qqq_weight, s.initial_investment, rf,
                    detail=False, equity_fraction=equity_fraction,
                ).summary

            profiles = conn.execute(text("SELECT profile_id, max_var95_1y_pct::float FROM risk_profile")).all()
            recs = [{"run_id": run_id, "profile_id": pid, **monte_carlo.recommend(summary, mv, simulate)}
                    for pid, mv in profiles]
            db.copy_df(conn, "recommendation", pd.DataFrame(recs))
        db.finish_run(run_id, "success", cal.end)
        best = summary.loc[summary["sharpe_median"].idxmax()]
        log.info("Monte Carlo run %d: unconstrained max-Sharpe at %.0f%% QQQ", run_id, best["qqq_weight"] * 100)
        return run_id
    except Exception as exc:
        db.finish_run(run_id, "failed", message=str(exc)[:1000])
        raise


# ==================================================================== A/B TEST ==
def run_ab(s: Settings) -> int:
    run_id = db.start_run("ab_test")
    try:
        pdaily = db.read_sql(
            """SELECT p.code, p.portfolio_id, d.trade_date, d.daily_return
               FROM portfolio_daily d JOIN portfolio p USING (portfolio_id)
               WHERE p.code IN ('SPY_60_QQQ_40', 'SPY_50_QQQ_50') AND d.daily_return IS NOT NULL""",
            parse_dates=["trade_date"],
        )
        wide = pdaily.pivot(index="trade_date", columns="code", values="daily_return").dropna()
        pid = pdaily.drop_duplicates("code").set_index("code")["portfolio_id"]
        rf = load_rf(wide.index)
        cfg = ab_test.ABConfig(s.ab_window_days, s.ab_step_days, s.bootstrap_iterations,
                               s.bootstrap_block, s.alpha, s.seed)
        rolling, results, boot = ab_test.run_ab_test(wide["SPY_60_QQQ_40"], wide["SPY_50_QQQ_50"], rf, cfg)
        for col in ("window_start", "window_end"):
            rolling[col] = pd.to_datetime(rolling[col]).dt.date

        with db.transaction() as conn:
            conn.execute(
                text("INSERT INTO ab_experiment VALUES (:r, :a, :b, :w, :st, :it, :bl, :ss, :se)"),
                {"r": run_id, "a": int(pid["SPY_60_QQQ_40"]), "b": int(pid["SPY_50_QQQ_50"]),
                 "w": cfg.window_days, "st": cfg.step_days, "it": cfg.bootstrap_iterations,
                 "bl": cfg.block_length, "ss": wide.index.min().date(), "se": wide.index.max().date()},
            )
            db.copy_df(conn, "ab_rolling_window", rolling.assign(run_id=run_id))
            db.copy_df(conn, "ab_test_result", results.assign(run_id=run_id))
            db.copy_df(conn, "ab_bootstrap_sample", boot.assign(run_id=run_id))
        db.finish_run(run_id, "success", wide.index.max().date())
        log.info("A/B test run %d:\n%s", run_id, results[["test_name", "estimate", "p_value"]].to_string(index=False))
        return run_id
    except Exception as exc:
        db.finish_run(run_id, "failed", message=str(exc)[:1000])
        raise


# ========================================================== ML VOLATILITY MODEL ==
def run_ml(s: Settings) -> int:
    run_id = db.start_run("ml_vol")
    try:
        prices = load_prices()
        vix = prices[prices["symbol"] == "^VIX"].set_index("trade_date")["close"]
        metric_rows, pred_rows, fc_rows, as_of = [], [], [], None
        with db.transaction() as conn:
            ids = db.asset_ids(conn)
        for symbol in s.core_symbols:
            px = prices[prices["symbol"] == symbol].set_index("trade_date")["adj_close"]
            feats = vol_model.build_features(np.log(px).diff(), px, vix if not vix.empty else None)
            m, p, fc = vol_model.walk_forward(feats)
            metric_rows.append(m.assign(asset_id=ids[symbol]))
            pred_rows.append(p.assign(asset_id=ids[symbol]))
            fc_rows += [{"asset_id": ids[symbol], "model_name": k, "forecast_vol": v} for k, v in fc.items()]
            as_of = px.index.max().date()
            pooled = m[m["fold"] == 0].set_index("model_name")["rmse"]
            log.info("ML vol %s pooled OOS RMSE: %s", symbol, pooled.round(4).to_dict())

        preds = pd.concat(pred_rows, ignore_index=True)
        preds["trade_date"] = pd.to_datetime(preds["trade_date"]).dt.date
        with db.transaction() as conn:
            db.copy_df(conn, "ml_model_metric", pd.concat(metric_rows, ignore_index=True).assign(run_id=run_id))
            db.copy_df(conn, "ml_vol_prediction", preds.assign(run_id=run_id))
            db.copy_df(conn, "ml_vol_forecast", pd.DataFrame(fc_rows).assign(run_id=run_id))
        db.finish_run(run_id, "success", as_of)
        return run_id
    except Exception as exc:
        db.finish_run(run_id, "failed", message=str(exc)[:1000])
        raise


# ================================================================= ORCHESTRATE ==
# Order matters: the Monte Carlo's "ML vol" scenario reads the ML model's forecast.
STEPS = {
    "extract": run_extract,
    "transform": run_transform,
    "ml": run_ml,
    "ab_test": run_ab,
    "monte_carlo": run_monte_carlo,
}


def run(steps: list[str] | None = None) -> None:
    s = get_settings()
    db.wait_for_db()
    db.ensure_schema()
    for name in steps or list(STEPS):
        t0 = time.monotonic()
        log.info("=== %s ===", name)
        STEPS[name](s)
        log.info("=== %s done in %.1fs ===", name, time.monotonic() - t0)
    db.prune_runs(s.keep_runs)
