package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** Fail-closed sizing for a server-verified domestic PAPER signal. */
@Component
public class KisPaperSignalOrderSizer {
    private static final BigDecimal HUNDRED = new BigDecimal("100");
    private final KisLiveRiskLimits limits;
    private final TradingSafetyProperties trading;

    public KisPaperSignalOrderSizer(KisLiveRiskLimits limits, TradingSafetyProperties trading) {
        this.limits = limits;
        this.trading = trading;
    }

    public int sizeDomesticBuy(String symbol, BigDecimal price, BigDecimal stopDistance,
                               KisAccountSnapshot account) {
        if (symbol == null || !symbol.matches("\\d{6}") || price == null || price.signum() <= 0
                || stopDistance == null || stopDistance.signum() <= 0 || account == null
                || account.environment() != KisEnvironment.PAPER || account.market() != Market.KR
                || !Currency.KRW.name().equalsIgnoreCase(account.currency())
                || account.accountEquity() == null || account.accountEquity().signum() <= 0
                || account.orderableCash() == null || account.orderableCash().signum() <= 0
                || account.positions() == null) {
            throw new IllegalStateException("PAPER signal sizing requires a complete domestic PAPER account and risk snapshot.");
        }

        BigDecimal currentExposure = account.positions().stream()
                .filter(position -> position != null && symbol.equalsIgnoreCase(position.symbol()))
                .map(KisAccountSnapshot.Position::evaluationAmount)
                .filter(amount -> amount != null && amount.signum() > 0)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        boolean alreadyHeld = account.positions().stream().anyMatch(position -> position != null
                && position.symbol() != null && symbol.equalsIgnoreCase(position.symbol())
                && position.quantity() != null && position.quantity().signum() > 0);
        long openPositions = account.positions().stream().filter(position -> position != null
                        && position.symbol() != null && position.quantity() != null
                        && position.quantity().signum() > 0)
                .map(position -> position.symbol().toUpperCase(java.util.Locale.ROOT)).distinct().count();
        if (!alreadyHeld && openPositions >= limits.maxOpenPositions()) {
            throw new IllegalStateException("PAPER signal exceeds the maximum open-position count.");
        }
        BigDecimal equity = account.accountEquity();
        BigDecimal riskBudget = percent(equity, limits.tradeRiskPercent());
        BigDecimal positionBudget = percent(equity, limits.maxPositionPercent()).subtract(currentExposure);
        int byRisk = shares(riskBudget.divide(stopDistance, 0, RoundingMode.DOWN));
        int byPosition = shares(positionBudget.max(BigDecimal.ZERO).divide(price, 0, RoundingMode.DOWN));
        int byCash = shares(account.orderableCash().divide(price, 0, RoundingMode.DOWN));
        int byOrderLimit = shares(trading.maxOrderNotionalKrw().divide(price, 0, RoundingMode.DOWN));
        int quantity = Math.min(Math.min(byRisk, byPosition), Math.min(byCash, byOrderLimit));
        if (quantity < 1) {
            throw new IllegalStateException("PAPER signal quantity is zero under account and risk limits.");
        }
        return quantity;
    }

    private static BigDecimal percent(BigDecimal value, BigDecimal percent) {
        return value.multiply(percent).divide(HUNDRED, 8, RoundingMode.DOWN);
    }

    private static int shares(BigDecimal quantity) {
        if (quantity.signum() <= 0) return 0;
        return quantity.min(BigDecimal.valueOf(Integer.MAX_VALUE)).intValue();
    }
}
