CREATE TABLE kis_real_fill_snapshot (
    idempotency_key UUID NOT NULL REFERENCES kis_order_intent(idempotency_key) ON DELETE CASCADE,
    cumulative_filled_quantity INTEGER NOT NULL CHECK (cumulative_filled_quantity > 0),
    cumulative_average_fill_price NUMERIC(20, 6) NOT NULL CHECK (cumulative_average_fill_price > 0),
    remaining_quantity INTEGER NOT NULL CHECK (remaining_quantity >= 0),
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (idempotency_key, cumulative_filled_quantity)
);

CREATE INDEX idx_kis_real_fill_snapshot_observed_at
    ON kis_real_fill_snapshot (observed_at DESC);
