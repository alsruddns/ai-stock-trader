package com.aistocktrader.kis;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "kis_paper_exit_plan")
public class KisPaperExitPlan {
    @Id
    @Column(name = "entry_idempotency_key", nullable = false)
    private UUID entryIdempotencyKey;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 24)
    private KisPaperExitStatus status = KisPaperExitStatus.WAITING_FOR_ENTRY;
    @Column(name = "exit_reason", length = 24)
    private String exitReason;
    @Column(name = "exit_order_key")
    private UUID exitOrderKey;
    @Column(name = "exit_quantity")
    private Integer exitQuantity;
    @Column(name = "exit_limit_price", precision = 20, scale = 6)
    private BigDecimal exitLimitPrice;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected KisPaperExitPlan() { }

    public KisPaperExitPlan(UUID entryIdempotencyKey) {
        if (entryIdempotencyKey == null) throw new IllegalArgumentException("PAPER exit plan entry key is required.");
        this.entryIdempotencyKey = entryIdempotencyKey;
        this.updatedAt = Instant.now();
    }

    public UUID getEntryIdempotencyKey() { return entryIdempotencyKey; }
    public KisPaperExitStatus getStatus() { return status; }
    public String getExitReason() { return exitReason; }
    public UUID getExitOrderKey() { return exitOrderKey; }
    public Integer getExitQuantity() { return exitQuantity; }
    public BigDecimal getExitLimitPrice() { return exitLimitPrice; }
    public Instant getUpdatedAt() { return updatedAt; }
}
