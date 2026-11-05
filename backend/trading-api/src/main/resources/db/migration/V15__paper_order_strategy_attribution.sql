ALTER TABLE trade_order
    ADD COLUMN strategy_id VARCHAR(64),
    ADD COLUMN model_id VARCHAR(96);

ALTER TABLE paper_strategy_risk
    ADD COLUMN strategy_id VARCHAR(64),
    ADD COLUMN model_id VARCHAR(96);
