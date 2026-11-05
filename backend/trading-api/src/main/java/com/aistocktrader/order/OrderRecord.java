package com.aistocktrader.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Locale;
import java.util.UUID;

@Entity
@Table(name = "trade_order")
public class OrderRecord {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 8)
    private Market market;
    @Column(nullable = false, length = 24)
    private String symbol;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 8)
    private OrderSide side;
    @Column(nullable = false)
    private int quantity;
    @Column(name = "filled_quantity", nullable = false)
    private int filledQuantity;
    @Column(name = "limit_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal limitPrice;
    @Column(name = "average_fill_price", precision = 20, scale = 6)
    private BigDecimal averageFillPrice;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 8)
    private Currency currency;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 16)
    private OrderStatus status;
    @Column(name = "idempotency_key", nullable = false, unique = true)
    private UUID idempotencyKey;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected OrderRecord() {}
    public OrderRecord(OrderRequest request, UUID idempotencyKey) {
        market = request.market(); symbol = request.symbol().trim().toUpperCase(Locale.ROOT); side = request.side();
        quantity = request.quantity(); limitPrice = request.limitPrice(); currency = request.currency();
        status = OrderStatus.PENDING; this.idempotencyKey = idempotencyKey; createdAt = Instant.now(); updatedAt = createdAt;
    }
    public Long getId() { return id; }
    public Market getMarket() { return market; }
    public String getSymbol() { return symbol; }
    public OrderSide getSide() { return side; }
    public int getQuantity() { return quantity; }
    public BigDecimal getLimitPrice() { return limitPrice; }
    public Currency getCurrency() { return currency; }
    public OrderStatus getStatus() { return status; }
    public int getFilledQuantity() { return filledQuantity; }
    public BigDecimal getAverageFillPrice() { return averageFillPrice; }
    public UUID getIdempotencyKey() { return idempotencyKey; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void applyFill(int fillQuantity, BigDecimal fillPrice) {
        if (status == OrderStatus.FILLED || status == OrderStatus.CANCELLED || status == OrderStatus.REJECTED
                || status == OrderStatus.EXPIRED) {
            throw new IllegalArgumentException("Order is not open for additional fills.");
        }
        if (fillQuantity <= 0 || filledQuantity + fillQuantity > quantity || fillPrice == null || fillPrice.signum() <= 0) {
            throw new IllegalArgumentException("Fill quantity or price is invalid.");
        }
        BigDecimal total = averageFillPrice == null
                ? BigDecimal.ZERO
                : averageFillPrice.multiply(BigDecimal.valueOf(filledQuantity));
        filledQuantity += fillQuantity;
        averageFillPrice = total.add(fillPrice.multiply(BigDecimal.valueOf(fillQuantity)))
                .divide(BigDecimal.valueOf(filledQuantity), 6, java.math.RoundingMode.HALF_UP);
        status = filledQuantity == quantity ? OrderStatus.FILLED : OrderStatus.PARTIALLY_FILLED;
        updatedAt = Instant.now();
    }
}
