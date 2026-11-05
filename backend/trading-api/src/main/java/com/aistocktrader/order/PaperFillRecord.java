package com.aistocktrader.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "paper_fill")
public class PaperFillRecord {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "order_id", nullable = false)
    private Long orderId;
    @Column(name = "fill_key", nullable = false, unique = true)
    private UUID fillKey;
    @Column(nullable = false)
    private int quantity;
    @Column(name = "fill_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal fillPrice;
    @Column(name = "filled_at", nullable = false)
    private Instant filledAt;

    protected PaperFillRecord() {}
    public PaperFillRecord(Long orderId, PaperFillRequest request) {
        this.orderId = orderId;
        this.fillKey = request.fillKey();
        this.quantity = request.quantity();
        this.fillPrice = request.fillPrice();
        this.filledAt = Instant.now();
    }
    public Long getId() { return id; }
    public Long getOrderId() { return orderId; }
    public UUID getFillKey() { return fillKey; }
    public int getQuantity() { return quantity; }
    public BigDecimal getFillPrice() { return fillPrice; }
    public Instant getFilledAt() { return filledAt; }
}
