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
    @Column(name = "limit_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal limitPrice;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 8)
    private Currency currency;
    @Column(nullable = false, length = 16)
    private String status;
    @Column(name = "idempotency_key", nullable = false, unique = true)
    private UUID idempotencyKey;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected OrderRecord() {}
    public OrderRecord(OrderRequest request, UUID idempotencyKey) {
        market = request.market(); symbol = request.symbol().trim().toUpperCase(Locale.ROOT); side = request.side();
        quantity = request.quantity(); limitPrice = request.limitPrice(); currency = request.currency();
        status = "SIMULATED"; this.idempotencyKey = idempotencyKey; createdAt = Instant.now();
    }
    public Long getId() { return id; }
    public Market getMarket() { return market; }
    public String getSymbol() { return symbol; }
    public OrderSide getSide() { return side; }
    public int getQuantity() { return quantity; }
    public BigDecimal getLimitPrice() { return limitPrice; }
    public Currency getCurrency() { return currency; }
    public String getStatus() { return status; }
    public UUID getIdempotencyKey() { return idempotencyKey; }
    public Instant getCreatedAt() { return createdAt; }
}
