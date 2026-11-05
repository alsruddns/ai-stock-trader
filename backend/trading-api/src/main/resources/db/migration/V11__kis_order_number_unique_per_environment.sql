DROP INDEX uq_kis_order_intent_broker_order;

CREATE UNIQUE INDEX uq_kis_order_intent_environment_broker_order
    ON kis_order_intent (environment, broker_order_number)
    WHERE broker_order_number IS NOT NULL;
