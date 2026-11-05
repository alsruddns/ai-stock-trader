package com.aistocktrader.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Locale;

@Entity
@Table(name = "paper_position", uniqueConstraints =
        @UniqueConstraint(name = "uq_paper_position_instrument", columnNames = {"market", "symbol", "currency"}))
public class PaperPosition {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 2)
    private Market market;
    @Column(nullable = false, length = 24)
    private String symbol;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 3)
    private Currency currency;
    @Column(nullable = false)
    private int quantity;
    @Column(name = "average_cost", nullable = false, precision = 20, scale = 6)
    private BigDecimal averageCost;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PaperPosition() {}
    public PaperPosition(Market market, String symbol, Currency currency) {
        this.market = market;
        this.symbol = symbol.trim().toUpperCase(Locale.ROOT);
        this.currency = currency;
        this.quantity = 0;
        this.averageCost = BigDecimal.ZERO;
        this.updatedAt = Instant.now();
    }
    public void apply(OrderSide side, int fillQuantity, BigDecimal price) {
        if (side == OrderSide.BUY) {
            BigDecimal oldCost = averageCost.multiply(BigDecimal.valueOf(quantity));
            BigDecimal newCost = oldCost.add(price.multiply(BigDecimal.valueOf(fillQuantity)));
            quantity += fillQuantity;
            averageCost = newCost.divide(BigDecimal.valueOf(quantity), 6, java.math.RoundingMode.HALF_UP);
        } else {
            if (fillQuantity > quantity) throw new IllegalArgumentException("Paper sell exceeds the available position.");
            quantity -= fillQuantity;
            if (quantity == 0) averageCost = BigDecimal.ZERO;
        }
        updatedAt = Instant.now();
    }
    public Long getId() { return id; }
    public Market getMarket() { return market; }
    public String getSymbol() { return symbol; }
    public Currency getCurrency() { return currency; }
    public int getQuantity() { return quantity; }
    public BigDecimal getAverageCost() { return averageCost; }
    public Instant getUpdatedAt() { return updatedAt; }
}
