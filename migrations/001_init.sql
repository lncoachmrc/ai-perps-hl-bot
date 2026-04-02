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

COMMIT;
