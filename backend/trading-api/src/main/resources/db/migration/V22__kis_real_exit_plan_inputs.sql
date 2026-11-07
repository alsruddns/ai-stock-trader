ALTER TABLE kis_order_intent
    ADD COLUMN exit_stop_distance_per_share NUMERIC(20, 6),
    ADD COLUMN exit_reward_risk_multiple NUMERIC(10, 4);

ALTER TABLE kis_order_intent
    ADD CONSTRAINT chk_kis_order_intent_exit_plan
    CHECK (
        (exit_stop_distance_per_share IS NULL AND exit_reward_risk_multiple IS NULL)
        OR (environment = 'REAL' AND side = 'BUY'
            AND exit_stop_distance_per_share > 0 AND exit_reward_risk_multiple > 0)
    );
