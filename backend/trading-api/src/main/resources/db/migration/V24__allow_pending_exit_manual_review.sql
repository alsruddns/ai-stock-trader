ALTER TABLE kis_order_intent
    DROP CONSTRAINT chk_kis_order_intent_real_exit_plan_state;

ALTER TABLE kis_order_intent
    ADD CONSTRAINT chk_kis_order_intent_real_exit_plan_state
    CHECK (
        (real_exit_status = 'NOT_APPLICABLE'
            AND exit_stop_distance_per_share IS NULL AND exit_reward_risk_multiple IS NULL
            AND real_exit_reason IS NULL AND real_exit_order_key IS NULL
            AND real_exit_quantity IS NULL AND real_exit_limit_price IS NULL)
        OR (real_exit_status IN ('WAITING_FOR_ENTRY', 'ARMED')
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
        OR (real_exit_status = 'MANUAL_REVIEW'
            AND environment = 'REAL' AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0
            AND ((real_exit_reason IS NULL AND real_exit_order_key IS NULL
                  AND real_exit_quantity IS NULL AND real_exit_limit_price IS NULL)
                 OR (real_exit_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'SESSION_END')
                     AND real_exit_order_key IS NOT NULL AND real_exit_quantity > 0
                     AND real_exit_limit_price > 0)))
        OR (real_exit_status = 'CLOSED'
            AND environment = 'REAL' AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0)
    );
