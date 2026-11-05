CREATE TABLE kis_order_intent (
    idempotency_key UUID PRIMARY KEY,
    request_hash CHAR(64) NOT NULL,
    market VARCHAR(2) NOT NULL CHECK (market IN ('KR', 'US')),
    symbol VARCHAR(24) NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    limit_price NUMERIC(20, 6) NOT NULL CHECK (limit_price > 0),
    currency VARCHAR(3) NOT NULL CHECK (currency IN ('KRW', 'USD')),
    status VARCHAR(24) NOT NULL CHECK (status IN ('SUBMITTING', 'ACCEPTED', 'REJECTED', 'UNKNOWN', 'CANCEL_PENDING', 'CANCELLED')),
    broker_order_number VARCHAR(32),
    broker_organization_number VARCHAR(32),
    broker_response_code VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX uq_kis_order_intent_broker_order
    ON kis_order_intent (broker_order_number) WHERE broker_order_number IS NOT NULL;
CREATE INDEX idx_kis_order_intent_created_at ON kis_order_intent (created_at DESC);
