CREATE TABLE market_quote_snapshot (
    id BIGSERIAL PRIMARY KEY,
    market VARCHAR(2) NOT NULL,
    symbol VARCHAR(24) NOT NULL,
    exchange VARCHAR(8),
    currency VARCHAR(3) NOT NULL,
    price NUMERIC(24, 8) NOT NULL CHECK (price > 0),
    change_amount NUMERIC(24, 8),
    change_percent NUMERIC(12, 6),
    volume BIGINT,
    retrieved_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(40) NOT NULL
);

CREATE INDEX idx_market_quote_snapshot_symbol_time
    ON market_quote_snapshot (market, symbol, retrieved_at DESC);

CREATE TABLE account_balance_snapshot (
    id BIGSERIAL PRIMARY KEY,
    environment VARCHAR(8) NOT NULL CHECK (environment IN ('paper', 'real')),
    market VARCHAR(2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    summary JSONB NOT NULL,
    positions JSONB NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(40) NOT NULL
);

CREATE INDEX idx_account_balance_snapshot_env_market_time
    ON account_balance_snapshot (environment, market, retrieved_at DESC);
