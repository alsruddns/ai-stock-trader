CREATE TABLE paper_strategy_risk (
    position_id BIGINT PRIMARY KEY REFERENCES paper_position(id) ON DELETE CASCADE,
    stop_price NUMERIC(20, 6) NOT NULL,
    target_price NUMERIC(20, 6) NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_paper_strategy_risk_prices CHECK (stop_price > 0 AND target_price > stop_price)
);

ALTER TABLE paper_fill
    ADD COLUMN strategy_stop_price NUMERIC(20, 6),
    ADD COLUMN strategy_target_price NUMERIC(20, 6);

ALTER TABLE paper_fill
    ADD CONSTRAINT ck_paper_fill_strategy_risk_pair
    CHECK ((strategy_stop_price IS NULL AND strategy_target_price IS NULL)
        OR (strategy_stop_price > 0 AND strategy_target_price > strategy_stop_price));
