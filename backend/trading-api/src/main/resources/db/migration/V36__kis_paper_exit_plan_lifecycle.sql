CREATE TABLE kis_paper_exit_plan (
    entry_idempotency_key UUID PRIMARY KEY
        REFERENCES kis_order_intent(idempotency_key),
    status VARCHAR(24) NOT NULL DEFAULT 'WAITING_FOR_ENTRY',
    exit_reason VARCHAR(24),
    exit_order_key UUID,
    exit_quantity INTEGER,
    exit_limit_price NUMERIC(20, 6),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_kis_paper_exit_plan_status
        CHECK (status IN ('WAITING_FOR_ENTRY', 'ARMED', 'EXIT_PENDING', 'CLOSED', 'MANUAL_REVIEW')),
    CONSTRAINT chk_kis_paper_exit_plan_fields
        CHECK (
            (status IN ('WAITING_FOR_ENTRY', 'ARMED')
                AND exit_reason IS NULL AND exit_order_key IS NULL
                AND exit_quantity IS NULL AND exit_limit_price IS NULL)
            OR (status = 'EXIT_PENDING'
                AND exit_reason IS NOT NULL AND exit_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'SESSION_END')
                AND exit_order_key IS NOT NULL AND exit_quantity IS NOT NULL AND exit_quantity > 0
                AND exit_limit_price IS NOT NULL AND exit_limit_price > 0)
            OR (status IN ('CLOSED', 'MANUAL_REVIEW')
                AND (
                    (exit_reason IS NULL AND exit_order_key IS NULL
                        AND exit_quantity IS NULL AND exit_limit_price IS NULL)
                    OR (exit_reason IS NOT NULL AND exit_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'SESSION_END')
                        AND exit_order_key IS NOT NULL AND exit_quantity IS NOT NULL AND exit_quantity > 0
                        AND exit_limit_price IS NOT NULL AND exit_limit_price > 0)
                ))
        )
);

CREATE UNIQUE INDEX uq_kis_paper_exit_plan_exit_order_key
    ON kis_paper_exit_plan (exit_order_key) WHERE exit_order_key IS NOT NULL;

CREATE INDEX idx_kis_paper_exit_plan_monitor
    ON kis_paper_exit_plan (status, updated_at, entry_idempotency_key)
    WHERE status IN ('WAITING_FOR_ENTRY', 'ARMED', 'EXIT_PENDING');
