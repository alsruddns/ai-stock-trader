package com.aistocktrader.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import java.math.BigDecimal;

@ConfigurationProperties(prefix = "paper")
public record PaperTradingProperties(BigDecimal startingCashKrw, BigDecimal startingCashUsd,
                                     long orderTimeoutSeconds) {
    public PaperTradingProperties {
        startingCashKrw = startingCashKrw == null ? BigDecimal.ZERO : startingCashKrw;
        startingCashUsd = startingCashUsd == null ? BigDecimal.ZERO : startingCashUsd;
        if (startingCashKrw.signum() < 0 || startingCashUsd.signum() < 0) {
            throw new IllegalStateException("Paper starting cash cannot be negative.");
        }
        if (orderTimeoutSeconds < 0) throw new IllegalStateException("Paper order timeout cannot be negative.");
    }

    public BigDecimal forCurrency(com.aistocktrader.order.Currency currency) {
        return currency == com.aistocktrader.order.Currency.KRW ? startingCashKrw : startingCashUsd;
    }
}
