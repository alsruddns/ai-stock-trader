ALTER TABLE kis_order_intent
    ADD COLUMN exchange_code VARCHAR(8),
    ADD COLUMN filled_quantity INTEGER NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
    ADD COLUMN remaining_quantity INTEGER CHECK (remaining_quantity >= 0),
    ADD COLUMN average_fill_price NUMERIC(20, 6);

ALTER TABLE kis_order_intent DROP CONSTRAINT IF EXISTS kis_order_intent_status_check;
ALTER TABLE kis_order_intent
    ADD CONSTRAINT kis_order_intent_status_check CHECK (status IN (
        'SUBMITTING', 'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'REJECTED',
        'UNKNOWN', 'CANCEL_PENDING', 'CANCELLED'
    ));
