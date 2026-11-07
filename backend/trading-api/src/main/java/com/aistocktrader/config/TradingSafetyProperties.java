package com.aistocktrader.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;

import java.math.BigDecimal;

@ConfigurationProperties(prefix = "trading")
public record TradingSafetyProperties(String mode, boolean liveTradingEnabled, boolean realOrdersEnabled,
                                      boolean realProtectiveExitManagerEnabled,
                                      BigDecimal maxOrderNotionalKrw, BigDecimal maxOrderNotionalUsd) {
    public TradingSafetyProperties(String mode, boolean liveTradingEnabled,
                                   BigDecimal maxOrderNotionalKrw, BigDecimal maxOrderNotionalUsd) {
        this(mode, liveTradingEnabled, false, false, maxOrderNotionalKrw, maxOrderNotionalUsd);
    }

    public TradingSafetyProperties(String mode, boolean liveTradingEnabled, boolean realOrdersEnabled,
                                   BigDecimal maxOrderNotionalKrw, BigDecimal maxOrderNotionalUsd) {
        this(mode, liveTradingEnabled, realOrdersEnabled, false, maxOrderNotionalKrw, maxOrderNotionalUsd);
    }

    @ConstructorBinding
    public TradingSafetyProperties {
        mode = mode == null ? "paper" : mode.trim().toLowerCase(java.util.Locale.ROOT);
        maxOrderNotionalKrw = maxOrderNotionalKrw == null ? new BigDecimal("1000000") : maxOrderNotionalKrw;
        maxOrderNotionalUsd = maxOrderNotionalUsd == null ? new BigDecimal("100") : maxOrderNotionalUsd;
        if ("paper".equals(mode) && (liveTradingEnabled || realOrdersEnabled)) {
            throw new IllegalStateException("Paper mode requires both LIVE_TRADING_ENABLED and KIS_REAL_ORDERS_ENABLED to be false.");
        }
        if ("real".equals(mode) && (!liveTradingEnabled || !realOrdersEnabled)) {
            throw new IllegalStateException("Real mode requires TRADING_MODE=real, LIVE_TRADING_ENABLED=true, and KIS_REAL_ORDERS_ENABLED=true.");
        }
        if (!"paper".equals(mode) && !"real".equals(mode)) {
            throw new IllegalStateException("TRADING_MODE must be paper or real.");
        }
        if (maxOrderNotionalKrw.signum() <= 0 || maxOrderNotionalUsd.signum() <= 0) {
            throw new IllegalStateException("Order notional limits must be positive.");
        }
    }
}
