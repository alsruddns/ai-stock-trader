CREATE TABLE market_minute_bar (
    market VARCHAR(2) NOT NULL CHECK (market IN ('KR', 'US')),
    symbol VARCHAR(16) NOT NULL,
    bar_at TIMESTAMPTZ NOT NULL,
    open_price NUMERIC(24, 8) NOT NULL CHECK (open_price > 0),
    high_price NUMERIC(24, 8) NOT NULL CHECK (high_price > 0),
    low_price NUMERIC(24, 8) NOT NULL CHECK (low_price > 0),
    close_price NUMERIC(24, 8) NOT NULL CHECK (close_price > 0),
    volume BIGINT NOT NULL CHECK (volume >= 0),
    spread_bps NUMERIC(16, 8),
    source VARCHAR(40) NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_market_minute_bar PRIMARY KEY (market, symbol, bar_at),
    CONSTRAINT ck_market_minute_bar_spread CHECK (spread_bps IS NULL OR spread_bps >= 0),
    CONSTRAINT ck_market_minute_bar_ohlc CHECK (
        high_price >= GREATEST(open_price, low_price, close_price)
        AND low_price <= LEAST(open_price, high_price, close_price)
    )
);

CREATE INDEX ix_market_minute_bar_session ON market_minute_bar (market, bar_at DESC);
