package com.aistocktrader.kis;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;

@ConfigurationProperties(prefix = "trading.live-risk")
public record KisLiveRiskLimits(BigDecimal maxDailyLossPercent, BigDecimal tradeRiskPercent,
                                BigDecimal maxPositionPercent, int maxOpenPositions,
                                int maxConsecutiveLosses) {
    public KisLiveRiskLimits {
        maxDailyLossPercent = maxDailyLossPercent == null ? new BigDecimal("1.00") : maxDailyLossPercent;
        tradeRiskPercent = tradeRiskPercent == null ? new BigDecimal("0.25") : tradeRiskPercent;
        maxPositionPercent = maxPositionPercent == null ? new BigDecimal("10.00") : maxPositionPercent;
        if (maxDailyLossPercent.signum() <= 0 || maxDailyLossPercent.compareTo(new BigDecimal("100")) > 0
                || tradeRiskPercent.signum() <= 0 || tradeRiskPercent.compareTo(new BigDecimal("100")) > 0
                || maxPositionPercent.signum() <= 0 || maxPositionPercent.compareTo(new BigDecimal("100")) > 0
                || maxOpenPositions < 1 || maxConsecutiveLosses < 1) {
            throw new IllegalStateException("Live risk limits must be positive and position exposure cannot exceed 100%.");
        }
    }
}
