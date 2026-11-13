CREATE INDEX idx_kis_order_intent_stale_submitting
    ON kis_order_intent (updated_at)
    WHERE status = 'SUBMITTING';
