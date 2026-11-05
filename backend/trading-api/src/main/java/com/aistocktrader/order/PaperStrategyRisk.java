package com.aistocktrader.order;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "paper_strategy_risk")
public class PaperStrategyRisk {
    @Id
    @Column(name = "position_id")
    private Long positionId;
    @Column(name = "stop_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal stopPrice;
    @Column(name = "target_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal targetPrice;
    @Column(name = "opened_at", nullable = false)
    private Instant openedAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PaperStrategyRisk() { }

    public PaperStrategyRisk(Long positionId, BigDecimal stopPrice, BigDecimal targetPrice) {
        this.positionId = positionId;
        this.stopPrice = stopPrice;
        this.targetPrice = targetPrice;
        this.openedAt = Instant.now();
        this.updatedAt = openedAt;
    }

    public void update(BigDecimal stopPrice, BigDecimal targetPrice) {
        this.stopPrice = stopPrice;
        this.targetPrice = targetPrice;
        this.updatedAt = Instant.now();
    }

    public Long getPositionId() { return positionId; }
    public BigDecimal getStopPrice() { return stopPrice; }
    public BigDecimal getTargetPrice() { return targetPrice; }
    public Instant getOpenedAt() { return openedAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
