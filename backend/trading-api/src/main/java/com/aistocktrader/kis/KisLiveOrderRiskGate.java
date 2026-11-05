package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

@Component
public class KisLiveOrderRiskGate {
    private static final BigDecimal HUNDRED = new BigDecimal("100");
    private final TradingSafetyProperties tradingLimits;
    private final KisLiveRiskLimits limits;

    public KisLiveOrderRiskGate(TradingSafetyProperties tradingLimits, KisLiveRiskLimits limits) {
        this.tradingLimits = tradingLimits;
        this.limits = limits;
    }

    public KisLiveRiskDecision assess(OrderRequest request, String exchange,
                                      KisAccountSnapshot account, KisLiveRiskContext context) {
        List<String> reasons = new ArrayList<>();
        if (request == null || account == null || context == null) {
            return new KisLiveRiskDecision(false, List.of("REQUIRED_RISK_INPUT_MISSING"));
        }
        if (request.market() == null || request.side() == null || request.currency() == null
                || request.symbol() == null || request.symbol().isBlank()
                || request.quantity() <= 0 || request.limitPrice() == null || request.limitPrice().signum() <= 0) {
            reasons.add("INVALID_ORDER_REQUEST");
            return new KisLiveRiskDecision(false, reasons);
        }
        if (request.market() == com.aistocktrader.order.Market.US && !validUsExchange(exchange)) {
            reasons.add("VALID_US_EXCHANGE_REQUIRED");
        }
        boolean realAccount = account.environment() == KisEnvironment.REAL;
        boolean matchingMarket = account.market() == request.market();
        boolean matchingCurrency = accountCurrencyMatches(request.currency(), account.currency());
        boolean positionsValid = account.positions() != null
                && account.positions().stream().noneMatch(position -> position == null);
        if (!realAccount) reasons.add("REAL_ACCOUNT_REQUIRED");
        if (!matchingMarket) reasons.add("ACCOUNT_MARKET_MISMATCH");
        if (!matchingCurrency) reasons.add("ACCOUNT_CURRENCY_MISMATCH");
        if (!positionsValid) reasons.add("ACCOUNT_POSITIONS_INVALID");
        if (!context.accountSnapshotFresh()) reasons.add("FRESH_ACCOUNT_SNAPSHOT_REQUIRED");
        if (!context.quoteFresh()) reasons.add("FRESH_QUOTE_REQUIRED");
        if (!context.marketOpen()) reasons.add("MARKET_CLOSED");
        if (context.unresolvedOrderExists()) reasons.add("UNRESOLVED_ORDER_EXISTS");
        if (context.totalOpenPositions() < 0) reasons.add("OPEN_POSITION_COUNT_INVALID");
        if (!realAccount || !matchingMarket || !matchingCurrency || !positionsValid
                || !context.accountSnapshotFresh() || context.totalOpenPositions() < 0) {
            return new KisLiveRiskDecision(false, reasons);
        }
        BigDecimal notional = request.limitPrice().multiply(BigDecimal.valueOf(request.quantity()));
        BigDecimal maxNotional = request.currency() == Currency.KRW
                ? tradingLimits.maxOrderNotionalKrw() : tradingLimits.maxOrderNotionalUsd();
        if (notional.compareTo(maxNotional) > 0) reasons.add("ORDER_NOTIONAL_LIMIT");

        if (request.side() == OrderSide.SELL) {
            BigDecimal sellable = safePositions(account).stream()
                    .filter(position -> request.symbol().equalsIgnoreCase(position.symbol()))
                    .map(KisAccountSnapshot.Position::sellableQuantity)
                    .filter(java.util.Objects::nonNull).findFirst().orElse(BigDecimal.ZERO);
            if (sellable.compareTo(BigDecimal.valueOf(request.quantity())) < 0) reasons.add("INSUFFICIENT_SELLABLE_QUANTITY");
            return new KisLiveRiskDecision(reasons.isEmpty(), reasons);
        }

        assessBuy(request, account, context, notional, reasons);
        return new KisLiveRiskDecision(reasons.isEmpty(), reasons);
    }

    private void assessBuy(OrderRequest request, KisAccountSnapshot account,
                           KisLiveRiskContext context, BigDecimal notional, List<String> reasons) {
        if (!context.approvedModelCandidate()) reasons.add("APPROVED_MODEL_CANDIDATE_REQUIRED");
        if (context.dailyLossPercent() == null) reasons.add("DAILY_LOSS_BASELINE_REQUIRED");
        else if (context.dailyLossPercent().compareTo(limits.maxDailyLossPercent()) >= 0) reasons.add("DAILY_LOSS_LIMIT");
        if (context.consecutiveLosingTrades() < 0) reasons.add("CONSECUTIVE_LOSS_COUNT_INVALID");
        else if (context.consecutiveLosingTrades() >= limits.maxConsecutiveLosses()) reasons.add("CONSECUTIVE_LOSS_LIMIT");
        if (account.orderableCash() == null || account.orderableCash().compareTo(notional) < 0) reasons.add("INSUFFICIENT_ORDERABLE_CASH");
        if (context.accountEquity() == null || context.accountEquity().signum() <= 0) {
            reasons.add("ACCOUNT_EQUITY_REQUIRED");
            return;
        }

        BigDecimal currentPositionValue = safePositions(account).stream()
                .filter(position -> request.symbol().equalsIgnoreCase(position.symbol()))
                .map(KisAccountSnapshot.Position::evaluationAmount).filter(java.util.Objects::nonNull)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal positionLimit = context.accountEquity().multiply(limits.maxPositionPercent()).divide(HUNDRED, 6, java.math.RoundingMode.DOWN);
        if (currentPositionValue.add(notional).compareTo(positionLimit) > 0) reasons.add("POSITION_EXPOSURE_LIMIT");

        boolean alreadyHeld = account.positions().stream().anyMatch(position ->
                request.symbol().equalsIgnoreCase(position.symbol())
                        && position.quantity() != null && position.quantity().signum() > 0);
        if (!alreadyHeld && context.totalOpenPositions() >= limits.maxOpenPositions()) reasons.add("OPEN_POSITION_LIMIT");

        if (context.stopDistancePerShare() == null || context.stopDistancePerShare().signum() <= 0) {
            reasons.add("VALID_STOP_DISTANCE_REQUIRED");
        } else {
            BigDecimal tradeRisk = context.stopDistancePerShare().multiply(BigDecimal.valueOf(request.quantity()));
            BigDecimal riskLimit = context.accountEquity().multiply(limits.tradeRiskPercent()).divide(HUNDRED, 6, java.math.RoundingMode.DOWN);
            if (tradeRisk.compareTo(riskLimit) > 0) reasons.add("TRADE_RISK_LIMIT");
        }
    }

    private static boolean accountCurrencyMatches(Currency requested, String accountCurrency) {
        return accountCurrency != null && requested.name().equalsIgnoreCase(accountCurrency);
    }

    private static boolean validUsExchange(String exchange) {
        return exchange != null && List.of("NAS", "NASD", "NYS", "NYSE", "AMS", "AMEX")
                .contains(exchange.trim().toUpperCase(java.util.Locale.ROOT));
    }

    private static List<KisAccountSnapshot.Position> safePositions(KisAccountSnapshot account) {
        return account.positions() == null ? List.of() : account.positions().stream().filter(java.util.Objects::nonNull).toList();
    }
}
