ALTER TABLE kis_order_intent
    ADD COLUMN environment VARCHAR(8) NOT NULL DEFAULT 'PAPER'
        CHECK (environment IN ('PAPER', 'REAL'));

ALTER TABLE kis_order_intent
    ALTER COLUMN environment DROP DEFAULT;
