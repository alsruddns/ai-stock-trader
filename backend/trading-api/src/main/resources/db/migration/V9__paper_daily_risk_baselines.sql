CREATE TABLE paper_daily_risk_baseline (
    id BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL,
    currency VARCHAR(3) NOT NULL,
    equity NUMERIC(24, 6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_paper_risk_baseline_day_currency UNIQUE (session_date, currency),
    CONSTRAINT ck_paper_risk_baseline_equity CHECK (equity > 0)
);
