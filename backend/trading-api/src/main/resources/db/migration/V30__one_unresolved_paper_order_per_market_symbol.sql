-- Permit concurrent unresolved PAPER orders for different symbols while preventing
-- duplicate exposure for a symbol that is still being submitted or reconciled.
DROP INDEX uq_kis_paper_unresolved_order_per_market;

CREATE UNIQUE INDEX uq_kis_paper_unresolved_order_per_market_symbol
    ON kis_order_intent (market, symbol)
    WHERE environment = 'PAPER'
      AND status IN ('SUBMITTING', 'ACCEPTED', 'PARTIALLY_FILLED', 'CANCEL_PENDING', 'UNKNOWN');
