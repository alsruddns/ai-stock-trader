-- The application-level preflight is not atomic across concurrent requests.
-- Reserve at most one unresolved REAL order per market in the database before
-- any KIS write can be attempted. PAPER order lifecycles remain unaffected.
CREATE UNIQUE INDEX uq_kis_real_unresolved_order_per_market
    ON kis_order_intent (market)
    WHERE environment = 'REAL'
      AND status IN ('SUBMITTING', 'ACCEPTED', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'UNKNOWN');
