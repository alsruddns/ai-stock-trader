-- Keep a second PAPER submission from stacking exposure while an earlier broker
-- order is still unresolved. The application retries idempotency races separately.
CREATE UNIQUE INDEX uq_kis_paper_unresolved_order_per_market
    ON kis_order_intent (market)
    WHERE environment = 'PAPER'
      AND status IN ('SUBMITTING', 'ACCEPTED', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'UNKNOWN');
