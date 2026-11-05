package com.aistocktrader.order;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;

@ConfigurationProperties(prefix = "paper.costs")
public record PaperCostProperties(boolean enabled, BigDecimal krCommissionBps,
                                  BigDecimal krSellCostBps, BigDecimal usCommissionBps,
                                  BigDecimal usSellCostBps) {
    public PaperCostProperties {
        krCommissionBps = nonNegative(krCommissionBps, "KR commission");
        krSellCostBps = nonNegative(krSellCostBps, "KR sell cost");
        usCommissionBps = nonNegative(usCommissionBps, "US commission");
        usSellCostBps = nonNegative(usSellCostBps, "US sell cost");
        if (krCommissionBps.add(krSellCostBps).compareTo(new BigDecimal("10000")) >= 0
                || usCommissionBps.add(usSellCostBps).compareTo(new BigDecimal("10000")) >= 0) {
            throw new IllegalStateException("Paper trading costs must total less than 10000 basis points.");
        }
    }

    private static BigDecimal nonNegative(BigDecimal value, String name) {
        BigDecimal normalized = value == null ? BigDecimal.ZERO : value;
        if (normalized.signum() < 0) throw new IllegalStateException(name + " must be zero or positive.");
        return normalized;
    }
}
