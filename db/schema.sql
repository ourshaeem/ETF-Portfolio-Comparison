-- =============================================================================
-- ETF Portfolio Comparison — relational schema (PostgreSQL 16)
--
-- Normalisation: every table is in at least 2NF (most are 3NF).
--   * 1NF — atomic columns only (no arrays/JSON blobs), every table has a PK.
--   * 2NF — in every table with a composite key, each non-key column depends on
--     the WHOLE key. Attributes that depend on only part of a key live in their
--     own table, e.g.:
--       - fund name / expense ratio depend only on the asset   -> asset
--       - holding name depends only on the security            -> security
--       - forward realised vol depends only on (asset, date)   -> asset_feature_daily,
--         not on the ML prediction key (run, asset, model, date)
--       - simulation settings depend only on the run           -> mc_run_config,
--         not on the per-allocation key (run, qqq_weight)
--
-- The script is idempotent: the pipeline applies it on every start.
-- =============================================================================

-- ---------------------------------------------------------------- reference --
CREATE TABLE IF NOT EXISTS data_source (
    source_id   SMALLSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,            -- 'yfinance', 'alphavantage'
    base_url    TEXT
);

CREATE TABLE IF NOT EXISTS asset (
    asset_id        SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL UNIQUE,         -- 'SPY', 'QQQ', '^VIX'
    name            TEXT,
    asset_class     TEXT NOT NULL DEFAULT 'ETF',  -- 'ETF' | 'INDEX'
    category        TEXT,                         -- 'Large Blend', 'Large Growth'
    fund_family     TEXT,
    expense_ratio   NUMERIC(8, 6),                -- decimal, 0.0945% -> 0.000945
    inception_date  DATE,
    total_assets    NUMERIC(20, 2),
    is_core         BOOLEAN NOT NULL DEFAULT FALSE, -- SPY & QQQ = the question being answered
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sector (
    sector_id   SMALLSERIAL PRIMARY KEY,
    sector_key  TEXT NOT NULL UNIQUE,             -- 'technology'
    sector_name TEXT NOT NULL                     -- 'Technology'
);

CREATE TABLE IF NOT EXISTS security (
    security_id SERIAL PRIMARY KEY,
    symbol      TEXT NOT NULL UNIQUE,             -- constituent, e.g. 'NVDA'
    name        TEXT
);

CREATE TABLE IF NOT EXISTS risk_profile (
    profile_id          SMALLSERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,     -- 'Conservative'
    max_var95_1y_pct    NUMERIC(5, 2) NOT NULL,   -- max tolerated 1-yr 95% VaR, % of capital
    description         TEXT
);

-- --------------------------------------------------------------- raw / ETL --
CREATE TABLE IF NOT EXISTS price_daily (
    asset_id    INT  NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    trade_date  DATE NOT NULL,
    open        NUMERIC(14, 4),
    high        NUMERIC(14, 4),
    low         NUMERIC(14, 4),
    close       NUMERIC(14, 4) NOT NULL,
    adj_close   NUMERIC(14, 4) NOT NULL,
    volume      BIGINT,
    source_id   SMALLINT NOT NULL REFERENCES data_source(source_id),
    PRIMARY KEY (asset_id, trade_date)
);

CREATE TABLE IF NOT EXISTS dividend (
    asset_id    INT  NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    ex_date     DATE NOT NULL,
    amount      NUMERIC(12, 6) NOT NULL,
    PRIMARY KEY (asset_id, ex_date)
);

CREATE TABLE IF NOT EXISTS asset_sector_weight (
    asset_id    INT      NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    sector_id   SMALLINT NOT NULL REFERENCES sector(sector_id),
    as_of       DATE     NOT NULL,
    weight      NUMERIC(8, 6) NOT NULL,
    PRIMARY KEY (asset_id, sector_id, as_of)
);

CREATE TABLE IF NOT EXISTS asset_holding (
    asset_id    INT  NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    security_id INT  NOT NULL REFERENCES security(security_id),
    as_of       DATE NOT NULL,
    weight      NUMERIC(8, 6) NOT NULL,
    PRIMARY KEY (asset_id, security_id, as_of)
);

CREATE TABLE IF NOT EXISTS risk_free_rate (
    rate_date   DATE PRIMARY KEY,
    annual_rate NUMERIC(8, 6) NOT NULL,           -- decimal, 4.23% -> 0.0423
    source_id   SMALLINT NOT NULL REFERENCES data_source(source_id)
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    log_id      BIGSERIAL PRIMARY KEY,
    logged_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_id   SMALLINT REFERENCES data_source(source_id),
    dataset     TEXT NOT NULL,                    -- 'prices:SPY', 'risk_free', ...
    rows_loaded INT  NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,                    -- 'ok' | 'fallback' | 'error'
    message     TEXT
);

CREATE TABLE IF NOT EXISTS live_quote (
    asset_id        INT NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    quote_ts        TIMESTAMPTZ NOT NULL,
    price           NUMERIC(14, 4) NOT NULL,
    previous_close  NUMERIC(14, 4),
    PRIMARY KEY (asset_id, quote_ts)
);

-- --------------------------------------------------------- derived features --
CREATE TABLE IF NOT EXISTS asset_feature_daily (
    asset_id                INT  NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    trade_date              DATE NOT NULL,
    simple_return           DOUBLE PRECISION,
    log_return              DOUBLE PRECISION,
    growth_of_50k           DOUBLE PRECISION,     -- $50k invested at HISTORY_START
    drawdown                DOUBLE PRECISION,     -- vs running peak, <= 0
    rolling_vol_63d         DOUBLE PRECISION,     -- annualised
    rolling_return_252d     DOUBLE PRECISION,
    rolling_sharpe_252d     DOUBLE PRECISION,
    ttm_dividend_yield      DOUBLE PRECISION,
    fwd_realized_vol_21d    DOUBLE PRECISION,     -- target for the ML model
    PRIMARY KEY (asset_id, trade_date)
);

CREATE TABLE IF NOT EXISTS portfolio (
    portfolio_id    SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,          -- 'SPY_60_QQQ_40'
    name            TEXT NOT NULL,
    description     TEXT,
    rebalance_freq  TEXT NOT NULL DEFAULT 'monthly'
);

CREATE TABLE IF NOT EXISTS portfolio_weight (
    portfolio_id    INT NOT NULL REFERENCES portfolio(portfolio_id) ON DELETE CASCADE,
    asset_id        INT NOT NULL REFERENCES asset(asset_id),
    weight          NUMERIC(6, 4) NOT NULL CHECK (weight BETWEEN 0 AND 1),
    PRIMARY KEY (portfolio_id, asset_id)
);

CREATE TABLE IF NOT EXISTS portfolio_daily (
    portfolio_id    INT  NOT NULL REFERENCES portfolio(portfolio_id) ON DELETE CASCADE,
    trade_date      DATE NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    daily_return    DOUBLE PRECISION,
    drawdown        DOUBLE PRECISION,
    rolling_vol_63d DOUBLE PRECISION,
    PRIMARY KEY (portfolio_id, trade_date)
);

-- Trailing-window scorecards ('1Y','3Y','5Y','10Y','MAX') for assets and portfolios.
CREATE TABLE IF NOT EXISTS asset_period_stat (
    asset_id        INT  NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    period          TEXT NOT NULL,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    total_return    DOUBLE PRECISION,
    cagr            DOUBLE PRECISION,
    vol_annual      DOUBLE PRECISION,
    sharpe          DOUBLE PRECISION,
    sortino         DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,             -- magnitude, positive
    var95_daily     DOUBLE PRECISION,             -- historical, magnitude
    cvar95_daily    DOUBLE PRECISION,
    PRIMARY KEY (asset_id, period)
);

CREATE TABLE IF NOT EXISTS portfolio_period_stat (
    portfolio_id    INT  NOT NULL REFERENCES portfolio(portfolio_id) ON DELETE CASCADE,
    period          TEXT NOT NULL,
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    total_return    DOUBLE PRECISION,
    cagr            DOUBLE PRECISION,
    vol_annual      DOUBLE PRECISION,
    sharpe          DOUBLE PRECISION,
    sortino         DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    var95_daily     DOUBLE PRECISION,
    cvar95_daily    DOUBLE PRECISION,
    PRIMARY KEY (portfolio_id, period)
);

-- ------------------------------------------------------------- model runs --
CREATE TABLE IF NOT EXISTS model_run (
    run_id      SERIAL PRIMARY KEY,
    run_type    TEXT NOT NULL,                    -- 'monte_carlo' | 'ab_test' | 'ml_vol'
    status      TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'success' | 'failed'
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    as_of_date  DATE,                             -- last market date in the data used
    message     TEXT
);
CREATE INDEX IF NOT EXISTS ix_model_run_type ON model_run (run_type, status, run_id DESC);

-- Monte Carlo ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mc_run_config (
    run_id              INT PRIMARY KEY REFERENCES model_run(run_id) ON DELETE CASCADE,
    n_paths             INT NOT NULL,
    horizon_years       INT NOT NULL,
    steps_per_year      INT NOT NULL,
    initial_investment  NUMERIC(14, 2) NOT NULL,
    calibration_start   DATE NOT NULL,
    calibration_end     DATE NOT NULL,
    risk_free_annual    DOUBLE PRECISION NOT NULL,
    weight_step         NUMERIC(4, 3) NOT NULL,
    seed                INT NOT NULL
);

CREATE TABLE IF NOT EXISTS mc_asset_param (
    run_id          INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    asset_id        INT NOT NULL REFERENCES asset(asset_id),
    mu_annual       DOUBLE PRECISION NOT NULL,    -- arithmetic drift, annualised
    sigma_annual    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, asset_id)
);

CREATE TABLE IF NOT EXISTS mc_asset_correlation (
    run_id      INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    asset_id_a  INT NOT NULL REFERENCES asset(asset_id),
    asset_id_b  INT NOT NULL REFERENCES asset(asset_id),
    correlation DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, asset_id_a, asset_id_b)
);

CREATE TABLE IF NOT EXISTS mc_allocation_summary (
    run_id              INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    qqq_weight          NUMERIC(4, 3) NOT NULL,
    exp_return_annual   DOUBLE PRECISION,         -- ex-ante arithmetic
    vol_annual          DOUBLE PRECISION,         -- ex-ante
    sharpe_ex_ante      DOUBLE PRECISION,
    sharpe_median       DOUBLE PRECISION,         -- median realised 5-yr Sharpe across paths
    sortino_median      DOUBLE PRECISION,
    cagr_median         DOUBLE PRECISION,
    terminal_mean       DOUBLE PRECISION,
    terminal_p05        DOUBLE PRECISION,
    terminal_p50        DOUBLE PRECISION,
    terminal_p95        DOUBLE PRECISION,
    var95_1y            DOUBLE PRECISION,         -- $ loss not exceeded with 95% conf. after 1 yr
    var95_1y_pct        DOUBLE PRECISION,         -- same, % of initial capital
    cvar95_1y           DOUBLE PRECISION,
    var95_horizon       DOUBLE PRECISION,         -- over the full horizon (can be negative = gain)
    mdd_median          DOUBLE PRECISION,         -- magnitudes, positive numbers
    mdd_p95             DOUBLE PRECISION,         -- 95th-percentile worst drawdown
    prob_loss_horizon   DOUBLE PRECISION,
    PRIMARY KEY (run_id, qqq_weight)
);

-- Sensitivity analysis: the same frontier under alternative calibrations.
-- Scenario-level attributes depend on (run, scenario) only, so they live here and
-- not on the per-allocation rows in mc_scenario_frontier (2NF).
CREATE TABLE IF NOT EXISTS mc_scenario (
    run_id              INT  NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    scenario_code       TEXT NOT NULL,            -- 'full', '15y', 'equal_sharpe', 'ml_vol', ...
    label               TEXT NOT NULL,
    description         TEXT,
    calibration_start   DATE NOT NULL,
    calibration_end     DATE NOT NULL,
    spy_mu              DOUBLE PRECISION NOT NULL,
    spy_sigma           DOUBLE PRECISION NOT NULL,
    qqq_mu              DOUBLE PRECISION NOT NULL,
    qqq_sigma           DOUBLE PRECISION NOT NULL,
    correlation         DOUBLE PRECISION NOT NULL,
    max_sharpe_weight   NUMERIC(4, 3) NOT NULL,   -- unconstrained optimum under this scenario
    PRIMARY KEY (run_id, scenario_code)
);

CREATE TABLE IF NOT EXISTS mc_scenario_frontier (
    run_id              INT  NOT NULL,
    scenario_code       TEXT NOT NULL,
    qqq_weight          NUMERIC(4, 3) NOT NULL,
    exp_return_annual   DOUBLE PRECISION,
    vol_annual          DOUBLE PRECISION,
    sharpe_median       DOUBLE PRECISION,
    cagr_median         DOUBLE PRECISION,
    var95_1y_pct        DOUBLE PRECISION,
    mdd_p95             DOUBLE PRECISION,
    terminal_p50        DOUBLE PRECISION,
    PRIMARY KEY (run_id, scenario_code, qqq_weight),
    FOREIGN KEY (run_id, scenario_code) REFERENCES mc_scenario(run_id, scenario_code) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mc_fan (
    run_id      INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    qqq_weight  NUMERIC(4, 3) NOT NULL,
    step        INT NOT NULL,                     -- trading days from today
    p05         DOUBLE PRECISION NOT NULL,
    p25         DOUBLE PRECISION NOT NULL,
    p50         DOUBLE PRECISION NOT NULL,
    p75         DOUBLE PRECISION NOT NULL,
    p95         DOUBLE PRECISION NOT NULL,
    mean        DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, qqq_weight, step)
);

CREATE TABLE IF NOT EXISTS mc_terminal (
    run_id          INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    qqq_weight      NUMERIC(4, 3) NOT NULL,
    path_id         INT NOT NULL,
    terminal_value  DOUBLE PRECISION NOT NULL,
    value_1y        DOUBLE PRECISION NOT NULL,
    max_drawdown    DOUBLE PRECISION NOT NULL,     -- magnitude, positive
    PRIMARY KEY (run_id, qqq_weight, path_id)
);

CREATE TABLE IF NOT EXISTS mc_sample_path (
    run_id      INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    qqq_weight  NUMERIC(4, 3) NOT NULL,
    path_id     INT NOT NULL,
    step        INT NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, qqq_weight, path_id, step)
);

CREATE TABLE IF NOT EXISTS recommendation (
    run_id                  INT      NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    profile_id              SMALLINT NOT NULL REFERENCES risk_profile(profile_id),
    recommended_qqq_weight  NUMERIC(4, 3) NOT NULL,  -- QQQ share of the equity sleeve
    equity_fraction         NUMERIC(4, 3) NOT NULL,  -- share of $50k in SPY/QQQ; rest in T-bills
    within_tolerance        BOOLEAN NOT NULL,
    rationale               TEXT NOT NULL,
    -- metrics of the recommended portfolio itself (incl. any T-bill sleeve), so they
    -- depend on the whole key (run, profile) rather than on the allocation grid
    exp_return_annual       DOUBLE PRECISION,
    vol_annual              DOUBLE PRECISION,
    sharpe_median           DOUBLE PRECISION,
    cagr_median             DOUBLE PRECISION,
    var95_1y                DOUBLE PRECISION,
    var95_1y_pct            DOUBLE PRECISION,
    mdd_p95                 DOUBLE PRECISION,
    terminal_p05            DOUBLE PRECISION,
    terminal_p50            DOUBLE PRECISION,
    terminal_p95            DOUBLE PRECISION,
    PRIMARY KEY (run_id, profile_id)
);

-- A/B test ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ab_experiment (
    run_id                  INT PRIMARY KEY REFERENCES model_run(run_id) ON DELETE CASCADE,
    portfolio_a_id          INT NOT NULL REFERENCES portfolio(portfolio_id),
    portfolio_b_id          INT NOT NULL REFERENCES portfolio(portfolio_id),
    window_days             INT NOT NULL,
    step_days               INT NOT NULL,
    bootstrap_iterations    INT NOT NULL,
    block_length            INT NOT NULL,
    sample_start            DATE NOT NULL,
    sample_end              DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS ab_rolling_window (
    run_id          INT  NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    window_end      DATE NOT NULL,
    window_start    DATE NOT NULL,
    return_a        DOUBLE PRECISION,             -- annualised
    return_b        DOUBLE PRECISION,
    vol_a           DOUBLE PRECISION,
    vol_b           DOUBLE PRECISION,
    sharpe_a        DOUBLE PRECISION,
    sharpe_b        DOUBLE PRECISION,
    mdd_a           DOUBLE PRECISION,
    mdd_b           DOUBLE PRECISION,
    tracking_error  DOUBLE PRECISION,
    PRIMARY KEY (run_id, window_end)
);

CREATE TABLE IF NOT EXISTS ab_test_result (
    run_id      INT  NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    test_name   TEXT NOT NULL,                    -- 'paired_t_rolling_sharpe', ...
    metric      TEXT NOT NULL,
    estimate    DOUBLE PRECISION,                 -- A minus B
    statistic   DOUBLE PRECISION,
    p_value     DOUBLE PRECISION,
    ci_low      DOUBLE PRECISION,
    ci_high     DOUBLE PRECISION,
    n_obs       INT,
    significant BOOLEAN,
    notes       TEXT,
    PRIMARY KEY (run_id, test_name)
);

CREATE TABLE IF NOT EXISTS ab_bootstrap_sample (
    run_id      INT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    iteration   INT NOT NULL,
    sharpe_diff DOUBLE PRECISION NOT NULL,
    return_diff DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, iteration)
);

-- ML volatility model ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS ml_model_metric (
    run_id      INT  NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    asset_id    INT  NOT NULL REFERENCES asset(asset_id),
    model_name  TEXT NOT NULL,                    -- 'naive_rv21', 'har_ols', 'gbm_regressor'
    fold        INT  NOT NULL,                    -- walk-forward fold, 0 = pooled out-of-sample
    rmse        DOUBLE PRECISION,
    mae         DOUBLE PRECISION,
    r2          DOUBLE PRECISION,
    qlike       DOUBLE PRECISION,
    n_obs       INT,
    PRIMARY KEY (run_id, asset_id, model_name, fold)
);

CREATE TABLE IF NOT EXISTS ml_vol_prediction (
    run_id          INT  NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    asset_id        INT  NOT NULL REFERENCES asset(asset_id),
    model_name      TEXT NOT NULL,
    trade_date      DATE NOT NULL,
    predicted_vol   DOUBLE PRECISION NOT NULL,     -- out-of-sample, annualised
    PRIMARY KEY (run_id, asset_id, model_name, trade_date)
);

CREATE TABLE IF NOT EXISTS ml_vol_forecast (
    run_id          INT  NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    asset_id        INT  NOT NULL REFERENCES asset(asset_id),
    model_name      TEXT NOT NULL,
    forecast_vol    DOUBLE PRECISION NOT NULL,     -- next 21 trading days, annualised
    PRIMARY KEY (run_id, asset_id, model_name)
);

-- AI analyst log ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_interaction (
    interaction_id  BIGSERIAL PRIMARY KEY,
    asked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INT,
    output_tokens   INT
);

-- ------------------------------------------------------------------- seeds --
INSERT INTO data_source (name, base_url) VALUES
    ('yfinance',     'https://query1.finance.yahoo.com'),
    ('alphavantage', 'https://www.alphavantage.co/query')
ON CONFLICT (name) DO NOTHING;

INSERT INTO risk_profile (name, max_var95_1y_pct, description) VALUES
    ('Conservative', 10, 'Can tolerate at most a 10% one-year loss in a bad (1-in-20) year'),
    ('Moderate',     20, 'Can tolerate at most a 20% one-year loss in a bad (1-in-20) year'),
    ('Aggressive',   30, 'Can tolerate at most a 30% one-year loss in a bad (1-in-20) year')
ON CONFLICT (name) DO NOTHING;
