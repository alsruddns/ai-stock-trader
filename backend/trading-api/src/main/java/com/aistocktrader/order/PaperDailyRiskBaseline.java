package com.aistocktrader.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;

@Entity
@Table(name = "paper_daily_risk_baseline", uniqueConstraints =
        @UniqueConstraint(name = "uq_paper_risk_baseline_day_currency", columnNames = {"session_date", "currency"}))
public class PaperDailyRiskBaseline {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "session_date", nullable = false)
    private LocalDate sessionDate;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 3)
    private Currency currency;
    @Column(nullable = false, precision = 24, scale = 6)
    private BigDecimal equity;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected PaperDailyRiskBaseline() { }

    public PaperDailyRiskBaseline(LocalDate sessionDate, Currency currency, BigDecimal equity) {
        this.sessionDate = sessionDate;
        this.currency = currency;
        this.equity = equity;
        this.createdAt = Instant.now();
    }

    public Long getId() { return id; }
    public LocalDate getSessionDate() { return sessionDate; }
    public Currency getCurrency() { return currency; }
    public BigDecimal getEquity() { return equity; }
    public Instant getCreatedAt() { return createdAt; }
}
