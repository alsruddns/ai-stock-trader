ALTER TABLE kis_order_intent
    ADD COLUMN real_exit_status VARCHAR(24) NOT NULL DEFAULT 'NOT_APPLICABLE',
    ADD COLUMN real_exit_reason VARCHAR(24),
    ADD COLUMN real_exit_order_key UUID,
    ADD COLUMN real_exit_quantity INTEGER,
    ADD COLUMN real_exit_limit_price NUMERIC(20, 6);

UPDATE kis_order_intent
   SET real_exit_status = CASE
       WHEN exit_stop_distance_per_share IS NULL OR exit_reward_risk_multiple IS NULL
           THEN 'NOT_APPLICABLE'
       WHEN environment <> 'REAL' OR side <> 'BUY' THEN 'NOT_APPLICABLE'
       WHEN COALESCE(filled_quantity, 0) = 0 AND status IN ('REJECTED', 'CANCELLED') THEN 'CLOSED'
       WHEN COALESCE(filled_quantity, 0) > 0 AND COALESCE(remaining_quantity, 0) = 0 THEN 'ARMED'
       ELSE 'WAITING_FOR_ENTRY'
   END;

ALTER TABLE kis_order_intent
    ADD CONSTRAINT chk_kis_order_intent_real_exit_status
    CHECK (real_exit_status IN ('NOT_APPLICABLE', 'WAITING_FOR_ENTRY', 'ARMED',
                                'EXIT_PENDING', 'CLOSED', 'MANUAL_REVIEW')),
    ADD CONSTRAINT chk_kis_order_intent_real_exit_plan_state
    CHECK (
        (real_exit_status = 'NOT_APPLICABLE'
            AND exit_stop_distance_per_share IS NULL AND exit_reward_risk_multiple IS NULL
            AND real_exit_reason IS NULL AND real_exit_order_key IS NULL
            AND real_exit_quantity IS NULL AND real_exit_limit_price IS NULL)
        OR (real_exit_status IN ('WAITING_FOR_ENTRY', 'ARMED', 'MANUAL_REVIEW')
            AND environment = 'REAL' AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0
            AND real_exit_reason IS NULL AND real_exit_order_key IS NULL
            AND real_exit_quantity IS NULL AND real_exit_limit_price IS NULL)
        OR (real_exit_status = 'EXIT_PENDING'
            AND environment = 'REAL' AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0
            AND real_exit_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'SESSION_END')
            AND real_exit_order_key IS NOT NULL AND real_exit_quantity > 0
            AND real_exit_limit_price > 0)
        OR (real_exit_status = 'CLOSED'
            AND environment = 'REAL' AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0)
    );

CREATE UNIQUE INDEX uq_kis_order_intent_real_exit_order_key
    ON kis_order_intent (real_exit_order_key) WHERE real_exit_order_key IS NOT NULL;

CREATE INDEX idx_kis_order_intent_real_exit_monitor
    ON kis_order_intent (market, symbol, updated_at)
    WHERE environment = 'REAL' AND side = 'BUY' AND real_exit_status IN ('ARMED', 'EXIT_PENDING');
