package com.aistocktrader.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.math.BigDecimal;

@ConfigurationProperties(prefix = "trading")
public record TradingSafetyProperties(String mode, boolean liveTradingEnabled,
                                      BigDecimal maxOrderNotionalKrw, BigDecimal maxOrderNotionalUsd) {
    public TradingSafetyProperties {
        mode = mode == null ? "paper" : mode;
        maxOrderNotionalKrw = maxOrderNotionalKrw == null ? new BigDecimal("1000000") : maxOrderNotionalKrw;
        maxOrderNotionalUsd = maxOrderNotionalUsd == null ? new BigDecimal("100") : maxOrderNotionalUsd;
        if (liveTradingEnabled) {
            throw new IllegalStateException("Live trading is unavailable in P1; LIVE_TRADING_ENABLED must be false.");
        }
        if (!"paper".equalsIgnoreCase(mode)) {
            throw new IllegalStateException("P1 supports paper mode only; set TRADING_MODE=paper.");
        }
        if (maxOrderNotionalKrw.signum() <= 0 || maxOrderNotionalUsd.signum() <= 0) {
            throw new IllegalStateException("Order notional limits must be positive.");
        }
    }
}
