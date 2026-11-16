ALTER TABLE kis_order_intent
    ADD COLUMN submission_response_code VARCHAR(32);

UPDATE kis_order_intent
SET submission_response_code = broker_response_code
WHERE status IN ('UNKNOWN', 'REJECTED')
  AND broker_response_code IS NOT NULL;
