CREATE TABLE paper_order_amendment (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES trade_order(id),
    idempotency_key UUID NOT NULL,
    old_quantity INTEGER NOT NULL CHECK (old_quantity > 0),
    new_quantity INTEGER NOT NULL CHECK (new_quantity > 0),
    old_limit_price NUMERIC(20, 6) NOT NULL CHECK (old_limit_price > 0),
    new_limit_price NUMERIC(20, 6) NOT NULL CHECK (new_limit_price > 0),
    amended_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_paper_order_amendment_key UNIQUE (order_id, idempotency_key)
);

CREATE INDEX ix_paper_order_amendment_order_time
    ON paper_order_amendment(order_id, amended_at DESC);
