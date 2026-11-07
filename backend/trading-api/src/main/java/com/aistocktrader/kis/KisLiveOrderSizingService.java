package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** Caps a proposed REAL buy quantity using server-side account and risk limits. */
@Component
public class KisLiveOrderSizingService {
    private static final BigDecimal HUNDRED = new BigDecimal("100");

    private final TradingSafetyProperties tradingLimits;
    private final KisLiveRiskLimits riskLimits;

    public KisLiveOrderSizingService(TradingSafetyProperties tradingLimits, KisLiveRiskLimits riskLimits) {
        this.tradingLimits = tradingLimits;
        this.riskLimits = riskLimits;
    }

    /**
     * The caller's quantity is only an upper bound. Missing or invalid risk inputs leave it unchanged so
     * the authoritative risk gate can reject the request; this method never increases requested exposure.
     */
    public OrderRequest capBuyQuantity(OrderRequest request, KisAccountSnapshot account,
                                       BigDecimal stopDistancePerShare) {
        if (request == null || account == null || request.market() == null || request.symbol() == null
                || request.symbol().isBlank() || request.currency() == null || request.side() != OrderSide.BUY
                || request.quantity() <= 0 || request.limitPrice() == null || request.limitPrice().signum() <= 0
                || stopDistancePerShare == null || stopDistancePerShare.signum() <= 0
                || account.accountEquity() == null || account.accountEquity().signum() <= 0
                || account.orderableCash() == null || account.orderableCash().signum() <= 0
                || account.positions() == null) {
            return request;
        }

        BigDecimal equity = account.accountEquity();
        BigDecimal price = request.limitPrice();
        BigDecimal currentSymbolExposure = account.positions().stream()
                .filter(position -> position != null && position.symbol() != null
                        && position.symbol().equalsIgnoreCase(request.symbol()))
                .map(KisAccountSnapshot.Position::evaluationAmount)
                .filter(value -> value != null && value.signum() > 0)
                .reduce(BigDecimal.ZERO, BigDecimal::add);

        BigDecimal riskBudget = percent(equity, riskLimits.tradeRiskPercent());
        BigDecimal positionBudget = percent(equity, riskLimits.maxPositionPercent()).subtract(currentSymbolExposure);
        BigDecimal maxNotional = request.currency() == Currency.KRW
                ? tradingLimits.maxOrderNotionalKrw() : tradingLimits.maxOrderNotionalUsd();

        int riskQuantity = wholeShares(riskBudget.divide(stopDistancePerShare, 0, RoundingMode.DOWN));
        int positionQuantity = wholeShares(positionBudget.max(BigDecimal.ZERO)
                .divide(price, 0, RoundingMode.DOWN));
        int cashQuantity = wholeShares(account.orderableCash().divide(price, 0, RoundingMode.DOWN));
        int notionalQuantity = wholeShares(maxNotional.divide(price, 0, RoundingMode.DOWN));
        int cappedQuantity = Math.min(request.quantity(), Math.min(riskQuantity,
                Math.min(positionQuantity, Math.min(cashQuantity, notionalQuantity))));

        if (cappedQuantity <= 0 || cappedQuantity == request.quantity()) return request;
        return new OrderRequest(request.market(), request.symbol(), request.side(), cappedQuantity,
                request.limitPrice(), request.currency(), request.strategyId(), request.modelId());
    }

    private static BigDecimal percent(BigDecimal base, BigDecimal percent) {
        return base.multiply(percent).divide(HUNDRED, 8, RoundingMode.DOWN);
    }

    private static int wholeShares(BigDecimal shares) {
        if (shares.signum() <= 0) return 0;
        return shares.min(BigDecimal.valueOf(Integer.MAX_VALUE)).intValue();
    }
}
