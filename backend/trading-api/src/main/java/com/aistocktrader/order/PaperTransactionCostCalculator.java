package com.aistocktrader.order;

import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;

@Component
public class PaperTransactionCostCalculator {
    private static final BigDecimal BASIS_POINTS = new BigDecimal("10000");
    private final PaperCostProperties properties;

    public PaperTransactionCostCalculator(PaperCostProperties properties) {
        this.properties = properties;
    }

    public BigDecimal calculate(Market market, OrderSide side, Currency currency, BigDecimal notional) {
        if (market == null || side == null || currency == null || notional == null || notional.signum() < 0) {
            throw new IllegalArgumentException("Market, side, currency, and a non-negative notional are required.");
        }
        if ((market == Market.KR && currency != Currency.KRW) || (market == Market.US && currency != Currency.USD)) {
            throw new IllegalArgumentException("Paper transaction cost currency must match its market.");
        }
        if (!properties.enabled()) return BigDecimal.ZERO.setScale(currency == Currency.KRW ? 0 : 2);
        BigDecimal commission = market == Market.KR ? properties.krCommissionBps() : properties.usCommissionBps();
        BigDecimal sellCosts = market == Market.KR ? properties.krSellCostBps() : properties.usSellCostBps();
        BigDecimal basisPoints = side == OrderSide.SELL ? commission.add(sellCosts) : commission;
        int scale = currency == Currency.KRW ? 0 : 2;
        return notional.multiply(basisPoints).divide(BASIS_POINTS, 8, RoundingMode.HALF_UP)
                .setScale(scale, RoundingMode.HALF_UP);
    }

    public boolean enabled() { return properties.enabled(); }

    public boolean automationAssumptionsReady(Market market) {
        if (!properties.enabled() || market == null) return false;
        return market == Market.KR
                ? properties.krCommissionBps().signum() > 0 && properties.krSellCostBps().signum() > 0
                : properties.usCommissionBps().signum() > 0 && properties.usSellCostBps().signum() > 0;
    }
}
