-- =============================================================================
-- Tableau-facing views (prefix tv_). Tableau Desktop connects LIVE to Postgres
-- and reads only these views, so every pipeline run is reflected on refresh with
-- no file exports. Model views always point at the latest successful run.
-- =============================================================================

-- Views hold no data, so drop and recreate them: lets view columns evolve freely.
DO $$
DECLARE v RECORD;
BEGIN
    FOR v IN SELECT viewname FROM pg_views WHERE schemaname = 'public' AND viewname LIKE 'tv\_%' LOOP
        EXECUTE format('DROP VIEW IF EXISTS %I CASCADE', v.viewname);
    END LOOP;
END
$$;

CREATE OR REPLACE FUNCTION latest_run_id(p_type TEXT) RETURNS INT
LANGUAGE sql STABLE AS $$
    SELECT max(run_id) FROM model_run WHERE run_type = p_type AND status = 'success'
$$;

-- ------------------------------------------------- Dashboard 1: performance --
CREATE OR REPLACE VIEW tv_price_history AS
SELECT a.symbol,
       a.name               AS fund_name,
       a.category,
       a.is_core,
       a.expense_ratio,
       p.trade_date,
       p.close::float       AS close,
       p.adj_close::float   AS adj_close,
       p.volume,
       f.simple_return,
       f.log_return,
       f.growth_of_50k,
       f.drawdown,
       f.rolling_vol_63d,
       f.rolling_return_252d,
       f.rolling_sharpe_252d,
       f.ttm_dividend_yield,
       d.amount::float      AS dividend_amount
FROM price_daily p
JOIN asset a                    ON a.asset_id = p.asset_id
LEFT JOIN asset_feature_daily f ON f.asset_id = p.asset_id AND f.trade_date = p.trade_date
LEFT JOIN dividend d            ON d.asset_id = p.asset_id AND d.ex_date = p.trade_date
WHERE a.asset_class = 'ETF';

CREATE OR REPLACE VIEW tv_asset_scorecard AS
SELECT a.symbol, a.name AS fund_name, a.is_core, a.expense_ratio,
       s.period, s.period_start, s.period_end, s.total_return, s.cagr, s.vol_annual,
       s.sharpe, s.sortino, s.max_drawdown, s.var95_daily, s.cvar95_daily
FROM asset_period_stat s
JOIN asset a ON a.asset_id = s.asset_id;

CREATE OR REPLACE VIEW tv_portfolio_history AS
SELECT p.code AS portfolio_code, p.name AS portfolio_name, p.rebalance_freq,
       d.trade_date, d.value, d.daily_return, d.drawdown, d.rolling_vol_63d
FROM portfolio_daily d
JOIN portfolio p ON p.portfolio_id = d.portfolio_id;

CREATE OR REPLACE VIEW tv_portfolio_scorecard AS
SELECT p.code AS portfolio_code, p.name AS portfolio_name,
       s.period, s.period_start, s.period_end, s.total_return, s.cagr, s.vol_annual,
       s.sharpe, s.sortino, s.max_drawdown, s.var95_daily, s.cvar95_daily
FROM portfolio_period_stat s
JOIN portfolio p ON p.portfolio_id = s.portfolio_id;

CREATE OR REPLACE VIEW tv_sector_exposure AS
SELECT a.symbol, s.sector_name, w.weight::float AS weight, w.as_of
FROM asset_sector_weight w
JOIN asset a  ON a.asset_id = w.asset_id
JOIN sector s ON s.sector_id = w.sector_id
WHERE w.as_of = (SELECT max(as_of) FROM asset_sector_weight x WHERE x.asset_id = w.asset_id);

CREATE OR REPLACE VIEW tv_top_holdings AS
SELECT a.symbol AS etf, s.symbol AS holding, s.name AS holding_name, h.weight::float AS weight, h.as_of
FROM asset_holding h
JOIN asset a    ON a.asset_id = h.asset_id
JOIN security s ON s.security_id = h.security_id
WHERE h.as_of = (SELECT max(as_of) FROM asset_holding x WHERE x.asset_id = h.asset_id);

CREATE OR REPLACE VIEW tv_risk_free AS
SELECT r.rate_date, r.annual_rate::float AS annual_rate, ds.name AS source
FROM risk_free_rate r
JOIN data_source ds ON ds.source_id = r.source_id;

-- --------------------------------------------- Dashboard 2: risk & Monte Carlo --
CREATE OR REPLACE VIEW tv_mc_frontier AS
SELECT s.run_id,
       r.as_of_date,
       s.qqq_weight::float                    AS qqq_weight,
       (1 - s.qqq_weight)::float              AS spy_weight,
       to_char(s.qqq_weight * 100, 'FM990') || '% QQQ / ' ||
       to_char((1 - s.qqq_weight) * 100, 'FM990') || '% SPY' AS allocation_label,
       s.exp_return_annual, s.vol_annual, s.sharpe_ex_ante, s.sharpe_median, s.sortino_median,
       s.cagr_median, s.terminal_mean, s.terminal_p05, s.terminal_p50, s.terminal_p95,
       s.var95_1y, s.var95_1y_pct, s.cvar95_1y, s.var95_horizon,
       s.mdd_median, s.mdd_p95, s.prob_loss_horizon,
       s.sharpe_median = max(s.sharpe_median) OVER () AS is_max_sharpe,
       c.n_paths, c.horizon_years, c.initial_investment::float AS initial_investment,
       c.risk_free_annual, c.calibration_start, c.calibration_end
FROM mc_allocation_summary s
JOIN model_run r     ON r.run_id = s.run_id
JOIN mc_run_config c ON c.run_id = s.run_id
WHERE s.run_id = latest_run_id('monte_carlo');

CREATE OR REPLACE VIEW tv_mc_fan AS
SELECT f.qqq_weight::float AS qqq_weight,
       f.step,
       round(f.step / 252.0, 3)::float AS years_ahead,
       (r.as_of_date + (f.step * 365.25 / 252)::int) AS projected_date,
       f.p05, f.p25, f.p50, f.p75, f.p95, f.mean
FROM mc_fan f
JOIN model_run r ON r.run_id = f.run_id
WHERE f.run_id = latest_run_id('monte_carlo');

CREATE OR REPLACE VIEW tv_mc_terminal AS
SELECT t.qqq_weight::float AS qqq_weight, t.path_id, t.terminal_value, t.value_1y, t.max_drawdown,
       t.terminal_value < c.initial_investment AS is_loss
FROM mc_terminal t
JOIN mc_run_config c ON c.run_id = t.run_id
WHERE t.run_id = latest_run_id('monte_carlo');

CREATE OR REPLACE VIEW tv_mc_sample_paths AS
SELECT sp.qqq_weight::float AS qqq_weight, sp.path_id, sp.step,
       (r.as_of_date + (sp.step * 365.25 / 252)::int) AS projected_date,
       sp.value
FROM mc_sample_path sp
JOIN model_run r ON r.run_id = sp.run_id
WHERE sp.run_id = latest_run_id('monte_carlo');

CREATE OR REPLACE VIEW tv_mc_parameters AS
SELECT a.symbol, p.mu_annual, p.sigma_annual,
       (SELECT correlation FROM mc_asset_correlation c
         WHERE c.run_id = p.run_id LIMIT 1) AS spy_qqq_correlation
FROM mc_asset_param p
JOIN asset a ON a.asset_id = p.asset_id
WHERE p.run_id = latest_run_id('monte_carlo');

CREATE OR REPLACE VIEW tv_mc_scenarios AS
SELECT sc.scenario_code, sc.label AS scenario, sc.description,
       sc.calibration_start, sc.calibration_end,
       sc.spy_mu, sc.spy_sigma, sc.qqq_mu, sc.qqq_sigma, sc.correlation,
       sc.max_sharpe_weight::float AS max_sharpe_weight,
       f.qqq_weight::float AS qqq_weight, f.exp_return_annual, f.vol_annual,
       f.sharpe_median, f.cagr_median, f.var95_1y_pct, f.mdd_p95, f.terminal_p50,
       f.qqq_weight = sc.max_sharpe_weight AS is_max_sharpe
FROM mc_scenario sc
JOIN mc_scenario_frontier f ON f.run_id = sc.run_id AND f.scenario_code = sc.scenario_code
WHERE sc.run_id = latest_run_id('monte_carlo');

-- --------------------------------------- Dashboard 3: A/B test & allocator --
CREATE OR REPLACE VIEW tv_recommendation AS
SELECT rp.name AS risk_profile, rp.max_var95_1y_pct::float AS max_var95_1y_pct, rp.description,
       rec.recommended_qqq_weight::float                         AS qqq_share_of_equity,
       rec.equity_fraction::float                                AS equity_fraction,
       (rec.equity_fraction * rec.recommended_qqq_weight)::float AS qqq_weight,
       (rec.equity_fraction * (1 - rec.recommended_qqq_weight))::float AS spy_weight,
       (1 - rec.equity_fraction)::float                          AS tbill_weight,
       round(c.initial_investment * rec.equity_fraction * rec.recommended_qqq_weight, 2)::float       AS qqq_dollars,
       round(c.initial_investment * rec.equity_fraction * (1 - rec.recommended_qqq_weight), 2)::float AS spy_dollars,
       round(c.initial_investment * (1 - rec.equity_fraction), 2)::float                              AS tbill_dollars,
       rec.within_tolerance, rec.rationale,
       rec.exp_return_annual, rec.vol_annual, rec.sharpe_median, rec.cagr_median,
       rec.var95_1y, rec.var95_1y_pct, rec.mdd_p95, rec.terminal_p05, rec.terminal_p50, rec.terminal_p95
FROM recommendation rec
JOIN risk_profile rp  ON rp.profile_id = rec.profile_id
JOIN mc_run_config c  ON c.run_id = rec.run_id
WHERE rec.run_id = latest_run_id('monte_carlo');

-- Robustness: Sharpe "regret" of each allocation vs the best allocation in each
-- calibration scenario. The minimax-regret weight is the ratio that is never far from
-- optimal whichever history you believe. (ML-vol is a stress test, not a calibration.)
CREATE OR REPLACE VIEW tv_mc_regret AS
WITH r AS (
    SELECT qqq_weight, scenario_code,
           max(sharpe_median) OVER (PARTITION BY scenario_code) - sharpe_median AS regret
    FROM tv_mc_scenarios
    WHERE scenario_code <> 'ml_vol'
)
SELECT qqq_weight, max(regret) AS max_regret, avg(regret) AS avg_regret,
       max(regret) = min(max(regret)) OVER () AS is_minimax_regret
FROM r
GROUP BY qqq_weight;

CREATE OR REPLACE VIEW tv_ab_rolling AS
SELECT w.window_start, w.window_end,
       pa.code AS portfolio_a, pb.code AS portfolio_b,
       w.return_a, w.return_b, w.vol_a, w.vol_b,
       w.sharpe_a, w.sharpe_b, w.sharpe_a - w.sharpe_b AS sharpe_diff,
       w.mdd_a, w.mdd_b, w.tracking_error
FROM ab_rolling_window w
JOIN ab_experiment e ON e.run_id = w.run_id
JOIN portfolio pa    ON pa.portfolio_id = e.portfolio_a_id
JOIN portfolio pb    ON pb.portfolio_id = e.portfolio_b_id
WHERE w.run_id = latest_run_id('ab_test');

CREATE OR REPLACE VIEW tv_ab_tests AS
SELECT t.test_name, t.metric, t.estimate, t.statistic, t.p_value, t.ci_low, t.ci_high,
       t.n_obs, t.significant, t.notes, e.sample_start, e.sample_end,
       e.bootstrap_iterations, e.window_days
FROM ab_test_result t
JOIN ab_experiment e ON e.run_id = t.run_id
WHERE t.run_id = latest_run_id('ab_test');

CREATE OR REPLACE VIEW tv_ab_bootstrap AS
SELECT iteration, sharpe_diff, return_diff
FROM ab_bootstrap_sample
WHERE run_id = latest_run_id('ab_test');

-- ------------------------------------------------------------ ML & live data --
CREATE OR REPLACE VIEW tv_ml_predictions AS
SELECT a.symbol, p.model_name, p.trade_date, p.predicted_vol,
       f.fwd_realized_vol_21d AS realized_vol
FROM ml_vol_prediction p
JOIN asset a ON a.asset_id = p.asset_id
LEFT JOIN asset_feature_daily f ON f.asset_id = p.asset_id AND f.trade_date = p.trade_date
WHERE p.run_id = latest_run_id('ml_vol');

CREATE OR REPLACE VIEW tv_ml_metrics AS
SELECT a.symbol, m.model_name, m.fold, m.rmse, m.mae, m.r2, m.qlike, m.n_obs
FROM ml_model_metric m
JOIN asset a ON a.asset_id = m.asset_id
WHERE m.run_id = latest_run_id('ml_vol');

CREATE OR REPLACE VIEW tv_ml_forecast AS
SELECT a.symbol, f.model_name, f.forecast_vol, r.as_of_date
FROM ml_vol_forecast f
JOIN asset a     ON a.asset_id = f.asset_id
JOIN model_run r ON r.run_id = f.run_id
WHERE f.run_id = latest_run_id('ml_vol');

CREATE OR REPLACE VIEW tv_live_quotes AS
SELECT a.symbol, q.quote_ts, q.price::float AS price, q.previous_close::float AS previous_close,
       (q.price / NULLIF(q.previous_close, 0) - 1)::float AS change_pct
FROM live_quote q
JOIN asset a ON a.asset_id = q.asset_id
WHERE q.quote_ts > now() - interval '7 days';

-- -------------------------------------------------- read-only Tableau login --
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tableau_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO tableau_reader';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO tableau_reader';
        EXECUTE 'GRANT EXECUTE ON FUNCTION latest_run_id(TEXT) TO tableau_reader';
    END IF;
END
$$;
