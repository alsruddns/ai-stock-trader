ALTER TABLE kis_order_intent DROP CONSTRAINT IF EXISTS kis_order_intent_status_check;
ALTER TABLE kis_order_intent
    ADD CONSTRAINT kis_order_intent_status_check CHECK (status IN (
        'SUBMITTING', 'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'REJECTED',
        'UNKNOWN', 'CANCEL_PENDING', 'CANCELLED', 'NO_ORDER_CONFIRMED'
    ));
