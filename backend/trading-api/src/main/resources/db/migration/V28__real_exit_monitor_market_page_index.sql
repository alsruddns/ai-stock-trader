CREATE INDEX idx_kis_order_intent_real_exit_page
    ON kis_order_intent (market, updated_at, idempotency_key)
    WHERE environment = 'REAL'
      AND side = 'BUY'
      AND real_exit_status IN ('ARMED', 'EXIT_PENDING');
