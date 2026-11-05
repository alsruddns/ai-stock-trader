ALTER TABLE kis_order_intent
    ALTER COLUMN request_hash TYPE VARCHAR(64)
    USING BTRIM(request_hash)::VARCHAR(64);
