CREATE TABLE paper_cash_balance (
    currency VARCHAR(3) PRIMARY KEY CHECK (currency IN ('KRW', 'USD')),
    balance NUMERIC(24, 6) NOT NULL CHECK (balance >= 0),
    updated_at TIMESTAMPTZ NOT NULL
);
