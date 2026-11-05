package com.aistocktrader.order;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "paper_cash_balance")
public class PaperCashBalance {
    @Id @Enumerated(EnumType.STRING) @Column(nullable = false, length = 3)
    private Currency currency;
    @Column(nullable = false, precision = 24, scale = 6)
    private BigDecimal balance;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PaperCashBalance() {}
    public PaperCashBalance(Currency currency, BigDecimal openingBalance) {
        this.currency = currency;
        this.balance = openingBalance;
        this.updatedAt = Instant.now();
    }
    public void debit(BigDecimal amount) {
        if (amount.signum() <= 0 || balance.compareTo(amount) < 0) {
            throw new IllegalArgumentException("Paper cash balance is insufficient.");
        }
        balance = balance.subtract(amount);
        updatedAt = Instant.now();
    }
    public void credit(BigDecimal amount) {
        if (amount.signum() <= 0) throw new IllegalArgumentException("Cash movement must be positive.");
        balance = balance.add(amount);
        updatedAt = Instant.now();
    }
    public Currency getCurrency() { return currency; }
    public BigDecimal getBalance() { return balance; }
    public Instant getUpdatedAt() { return updatedAt; }
}
