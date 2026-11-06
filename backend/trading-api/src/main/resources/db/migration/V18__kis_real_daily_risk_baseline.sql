CREATE TABLE kis_real_daily_risk_baseline (
    market VARCHAR(2) NOT NULL CHECK (market IN ('KR', 'US')),
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('KRW', 'USD')),
    account_fingerprint CHAR(64) NOT NULL,
    session_date DATE NOT NULL,
    account_equity NUMERIC(24, 6) NOT NULL CHECK (account_equity > 0),
    captured_at TIMESTAMPTZ NOT NULL,
    source VARCHAR(40) NOT NULL CHECK (source = 'KIS_READ_ONLY_REST'),
    CONSTRAINT pk_kis_real_daily_risk_baseline PRIMARY KEY (market, session_date, account_fingerprint)
);
