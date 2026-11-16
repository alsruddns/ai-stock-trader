ALTER TABLE kis_order_intent
    DROP CONSTRAINT chk_kis_order_intent_exit_plan;

ALTER TABLE kis_order_intent
    ADD CONSTRAINT chk_kis_order_intent_exit_plan
    CHECK (
        (exit_stop_distance_per_share IS NULL AND exit_reward_risk_multiple IS NULL)
        OR (environment IN ('REAL', 'PAPER') AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0
            AND limit_price > exit_stop_distance_per_share)
    );
