package com.aistocktrader.order;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "paper_order_amendment")
public class PaperOrderAmendment {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "order_id", nullable = false)
    private Long orderId;
    @Column(name = "idempotency_key", nullable = false)
    private UUID idempotencyKey;
    @Column(name = "old_quantity", nullable = false)
    private int oldQuantity;
    @Column(name = "new_quantity", nullable = false)
    private int newQuantity;
    @Column(name = "old_limit_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal oldLimitPrice;
    @Column(name = "new_limit_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal newLimitPrice;
    @Column(name = "amended_at", nullable = false)
    private Instant amendedAt;

    protected PaperOrderAmendment() { }

    public PaperOrderAmendment(Long orderId, UUID idempotencyKey, int oldQuantity, int newQuantity,
                               BigDecimal oldLimitPrice, BigDecimal newLimitPrice) {
        this.orderId = orderId;
        this.idempotencyKey = idempotencyKey;
        this.oldQuantity = oldQuantity;
        this.newQuantity = newQuantity;
        this.oldLimitPrice = oldLimitPrice;
        this.newLimitPrice = newLimitPrice;
        this.amendedAt = Instant.now();
    }

    public Long getOrderId() { return orderId; }
    public UUID getIdempotencyKey() { return idempotencyKey; }
    public int getOldQuantity() { return oldQuantity; }
    public int getNewQuantity() { return newQuantity; }
    public BigDecimal getOldLimitPrice() { return oldLimitPrice; }
    public BigDecimal getNewLimitPrice() { return newLimitPrice; }
}
