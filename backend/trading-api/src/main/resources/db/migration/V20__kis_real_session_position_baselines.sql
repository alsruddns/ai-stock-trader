CREATE TABLE kis_real_session_position_baseline (
    market VARCHAR(2) NOT NULL CHECK (market IN ('KR', 'US')),
    session_date DATE NOT NULL,
    account_fingerprint CHAR(64) NOT NULL,
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('KRW', 'USD')),
    captured_at TIMESTAMPTZ NOT NULL,
    position_count INTEGER NOT NULL CHECK (position_count >= 0),
    source VARCHAR(40) NOT NULL CHECK (source = 'KIS_READ_ONLY_REST'),
    PRIMARY KEY (market, session_date, account_fingerprint)
);

CREATE TABLE kis_real_session_position_baseline_item (
    market VARCHAR(2) NOT NULL,
    session_date DATE NOT NULL,
    account_fingerprint CHAR(64) NOT NULL,
    symbol VARCHAR(24) NOT NULL,
    quantity NUMERIC(24, 6) NOT NULL CHECK (quantity > 0),
    average_purchase_price NUMERIC(20, 6) NOT NULL CHECK (average_purchase_price > 0),
    PRIMARY KEY (market, session_date, account_fingerprint, symbol),
    FOREIGN KEY (market, session_date, account_fingerprint)
        REFERENCES kis_real_session_position_baseline (market, session_date, account_fingerprint)
        ON DELETE CASCADE
);
