package com.aistocktrader.kis;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;

@ConfigurationProperties(prefix = "trading.live-risk")
public record KisLiveRiskLimits(BigDecimal maxDailyLossPercent, BigDecimal tradeRiskPercent,
                                BigDecimal maxPositionPercent, int maxOpenPositions,
                                int maxConsecutiveLosses) {
    private static final BigDecimal APPROVED_MAX_DAILY_LOSS_PERCENT = new BigDecimal("1.00");
    private static final BigDecimal APPROVED_MAX_TRADE_RISK_PERCENT = new BigDecimal("0.25");
    private static final BigDecimal APPROVED_MAX_POSITION_PERCENT = new BigDecimal("10.00");
    private static final int APPROVED_MAX_OPEN_POSITIONS = 3;
    private static final int APPROVED_MAX_CONSECUTIVE_LOSSES = 3;

    public KisLiveRiskLimits {
        maxDailyLossPercent = maxDailyLossPercent == null ? new BigDecimal("1.00") : maxDailyLossPercent;
        tradeRiskPercent = tradeRiskPercent == null ? new BigDecimal("0.25") : tradeRiskPercent;
        maxPositionPercent = maxPositionPercent == null ? new BigDecimal("10.00") : maxPositionPercent;
        if (maxDailyLossPercent.signum() <= 0 || maxDailyLossPercent.compareTo(APPROVED_MAX_DAILY_LOSS_PERCENT) > 0
                || tradeRiskPercent.signum() <= 0 || tradeRiskPercent.compareTo(APPROVED_MAX_TRADE_RISK_PERCENT) > 0
                || maxPositionPercent.signum() <= 0 || maxPositionPercent.compareTo(APPROVED_MAX_POSITION_PERCENT) > 0
                || maxOpenPositions < 1 || maxOpenPositions > APPROVED_MAX_OPEN_POSITIONS
                || maxConsecutiveLosses < 1 || maxConsecutiveLosses > APPROVED_MAX_CONSECUTIVE_LOSSES) {
            throw new IllegalStateException("Live risk limits cannot exceed the approved initial trading limits.");
        }
    }
}
