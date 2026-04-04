BEGIN;

CREATE TABLE IF NOT EXISTS decisions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    size_multiplier DOUBLE PRECISION,
    ttl_minutes INTEGER,
    reasons JSONB,
    stop_logic TEXT,
    take_profit_logic TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_asset_created_at ON decisions (asset, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_action_created_at ON decisions (action, created_at DESC);

CREATE TABLE IF NOT EXISTS journal_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    asset TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_journal_events_created_at ON journal_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_events_type_created_at ON journal_events (event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_events_asset_created_at ON journal_events (asset, created_at DESC);

CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset TEXT NOT NULL,
    side TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    entry_price NUMERIC(28, 10),
    exit_price NUMERIC(28, 10),
    size NUMERIC(28, 10),
    leverage NUMERIC(10, 4),
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_positions_asset_status ON positions (asset, status);
CREATE INDEX IF NOT EXISTS idx_positions_opened_at ON positions (opened_at DESC);

CREATE TABLE IF NOT EXISTS news_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    asset TEXT,
    impact TEXT,
    direction TEXT,
    title TEXT,
    url TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_news_events_created_at ON news_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_source_created_at ON news_events (source, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_events_asset_created_at ON news_events (asset, created_at DESC);


CREATE TABLE IF NOT EXISTS market_observations (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset TEXT NOT NULL,
    mark_price DOUBLE PRECISION NOT NULL,
    spread_bps DOUBLE PRECISION,
    funding_rate DOUBLE PRECISION,
    open_interest_delta_1h DOUBLE PRECISION,
    regime_hint TEXT,
    position_side TEXT,
    decision_action TEXT,
    risk_gate_final_action TEXT,
    setup_score DOUBLE PRECISION,
    signal_strength DOUBLE PRECISION,
    p_up DOUBLE PRECISION,
    p_down DOUBLE PRECISION,
    expected_move_60m DOUBLE PRECISION,
    invalidation_price DOUBLE PRECISION,
    prophet_trend_bias TEXT,
    forecast_delta_4h DOUBLE PRECISION,
    news_impact TEXT,
    news_direction TEXT,
    tradability_flag TEXT,
    cost_estimate_bps DOUBLE PRECISION,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_market_observations_asset_observed_at
    ON market_observations (asset, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_observations_action_observed_at
    ON market_observations (decision_action, observed_at DESC);

CREATE TABLE IF NOT EXISTS decision_outcomes (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observation_id BIGINT NOT NULL REFERENCES market_observations(id) ON DELETE CASCADE,
    asset TEXT NOT NULL,
    effective_action TEXT NOT NULL,
    position_side TEXT,
    horizon_minutes INTEGER NOT NULL,
    reference_observed_at TIMESTAMPTZ NOT NULL,
    target_observed_at TIMESTAMPTZ NOT NULL,
    future_observed_at TIMESTAMPTZ NOT NULL,
    reference_price DOUBLE PRECISION NOT NULL,
    future_price DOUBLE PRECISION NOT NULL,
    future_return_pct DOUBLE PRECISION,
    mfe_pct DOUBLE PRECISION,
    mae_pct DOUBLE PRECISION,
    bars_observed INTEGER NOT NULL DEFAULT 0,
    neutral_band_pct DOUBLE PRECISION,
    outcome_label TEXT NOT NULL,
    outcome_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_outcomes_observation_horizon
    ON decision_outcomes (observation_id, horizon_minutes);
CREATE INDEX IF NOT EXISTS idx_decision_outcomes_asset_horizon_created_at
    ON decision_outcomes (asset, horizon_minutes, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_decision_outcomes_label_created_at
    ON decision_outcomes (outcome_label, created_at DESC);

COMMIT;
