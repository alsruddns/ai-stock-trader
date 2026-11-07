package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** Optional REAL-only protective exits; disabled unless the dedicated automation switch is enabled. */
@Component
public class KisRealProtectiveExitMonitor {
    private static final Logger log = LoggerFactory.getLogger(KisRealProtectiveExitMonitor.class);
    private static final Duration MAX_ACCOUNT_AGE = Duration.ofSeconds(5);
    private static final Duration FLATTEN_BEFORE_CLOSE = Duration.ofMinutes(5);
    private static final List<KisRealExitStatus> MONITORED =
            List.of(KisRealExitStatus.ARMED, KisRealExitStatus.EXIT_PENDING);

    private final boolean automationEnabled;
    private final boolean reconciliationEnabled;
    private final TradingSafetyProperties trading;
    private final KisProperties kis;
    private final KisOrderIntentRepository intents;
    private final KisAccountClient accounts;
    private final KisMarketSessionClient sessions;
    private final KisMarketAiEvidenceClient marketData;
    private final KisRealOrderOrchestrator orders;
    private final KisRealExitPolicy policy = new KisRealExitPolicy(Duration.ofSeconds(3));

    public KisRealProtectiveExitMonitor(
            @Value("${kis.real-protective-exit-automation-enabled:false}") boolean automationEnabled,
            @Value("${kis.real-reconciliation-enabled:false}") boolean reconciliationEnabled,
            TradingSafetyProperties trading, KisProperties kis, KisOrderIntentRepository intents,
            KisAccountClient accounts, KisMarketSessionClient sessions,
            KisMarketAiEvidenceClient marketData, KisRealOrderOrchestrator orders) {
        this.automationEnabled = automationEnabled;
        this.reconciliationEnabled = reconciliationEnabled;
        this.trading = trading;
        this.kis = kis;
        this.intents = intents;
        this.accounts = accounts;
        this.sessions = sessions;
        this.marketData = marketData;
        this.orders = orders;
    }

    @Scheduled(fixedDelayString = "${kis.real-protective-exit-poll-ms:1000}")
    public void monitorRealPositions() {
        if (!enabled()) return;
        try {
            intents.findTop50ByEnvironmentAndRealExitStatusInOrderByUpdatedAtAsc(KisEnvironment.REAL, MONITORED)
                    .forEach(this::processSafely);
        } catch (RuntimeException exception) {
            log.warn("KIS REAL protective-exit scan failed ({})", exception.getClass().getSimpleName());
        }
    }

    private boolean enabled() {
        return automationEnabled && reconciliationEnabled && "real".equals(trading.mode()) && trading.liveTradingEnabled()
                && trading.realOrdersEnabled() && trading.realProtectiveExitManagerEnabled()
                && kis.accountReadEnabled() && kis.realAccountReadEnabled()
                && kis.credentials(KisEnvironment.REAL).configured();
    }

    private void processSafely(KisOrderIntent plan) {
        try {
            process(plan);
        } catch (RuntimeException exception) {
            log.warn("KIS REAL protective-exit check failed for {} {} ({})",
                    plan.getMarket(), plan.getSymbol(), exception.getClass().getSimpleName());
        }
    }

    private void process(KisOrderIntent plan) {
        if (plan.getRealExitStatus() == KisRealExitStatus.EXIT_PENDING) {
            List<KisOrderIntent> sameSymbolPlans = intents.findExitManagedRealBuyCandidates(
                    KisEnvironment.REAL, plan.getMarket(), plan.getSymbol());
            if (sameSymbolPlans.size() != 1) return;
            resumePendingExit(plan);
            return;
        }
        if (plan.getRealExitStatus() != KisRealExitStatus.ARMED) return;

        List<KisOrderIntent> sameSymbolPlans = intents.findExitManagedRealBuyCandidates(
                KisEnvironment.REAL, plan.getMarket(), plan.getSymbol());
        if (sameSymbolPlans.size() != 1) {
            intents.markRealExitManualReview(plan.getIdempotencyKey());
            return;
        }

        Instant now = Instant.now();
        KisMarketSessionClient.MarketSession session = sessions.session(plan.getMarket());
        if (!KisRealDailyRiskBaselineService.freshSession(session, plan.getMarket(), now)
                || !session.session() || !session.open() || session.openAt() == null || session.closeAt() == null
                || now.isBefore(session.openAt()) || !now.isBefore(session.closeAt())) return;

        KisMarketAiEvidenceClient.ExitQuote quote = marketData.quoteForExit(
                plan.getMarket(), plan.getSymbol(), plan.getExchangeCode());
        Instant decisionAt = Instant.now();
        KisRealExitPolicy.Decision decision = policy.evaluate(new KisRealExitPolicy.Input(
                plan.getFilledQuantity() == null ? 0 : plan.getFilledQuantity(), plan.getAverageFillPrice(),
                plan.getExitStopPrice(), plan.getExitTargetPrice(), quote.price(), quote.retrievedAt(), decisionAt,
                session.closeAt().minus(FLATTEN_BEFORE_CLOSE)));
        if (decision == KisRealExitPolicy.Decision.HOLD) return;
        if (decision != KisRealExitPolicy.Decision.STOP_LOSS
                && decision != KisRealExitPolicy.Decision.TAKE_PROFIT
                && decision != KisRealExitPolicy.Decision.SESSION_END) return;
        submitExit(plan, decision.name(), quote);
    }

    private void submitExit(KisOrderIntent plan, String reason, KisMarketAiEvidenceClient.ExitQuote quote) {
        BigDecimal limitPrice = quote.bestBid();
        if (!validBestBid(quote) || limitPrice == null) return;
        int quantity = plan.getFilledQuantity() == null ? 0 : plan.getFilledQuantity();
        if (quantity <= 0 || plan.getAverageFillPrice() == null) {
            intents.markRealExitManualReview(plan.getIdempotencyKey());
            return;
        }
        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, plan.getMarket(), null,
                null, plan.getExchangeCode());
        if (!freshRealAccount(account, plan.getMarket(), Instant.now())) return;
        List<KisAccountSnapshot.Position> matching = account.positions() == null ? List.of()
                : account.positions().stream().filter(position -> position != null
                        && plan.getSymbol().equalsIgnoreCase(position.symbol())).toList();
        if (matching.size() != 1 || !exactQuantity(matching.getFirst().quantity(), quantity)
                || !exactQuantity(matching.getFirst().sellableQuantity(), quantity)) {
            intents.markRealExitManualReview(plan.getIdempotencyKey());
            return;
        }

        UUID exitKey = UUID.randomUUID();
        if (intents.claimRealExit(plan.getIdempotencyKey(), exitKey, reason, quantity, limitPrice) != 1) return;
        OrderRequest sell = new OrderRequest(plan.getMarket(), plan.getSymbol(), OrderSide.SELL, quantity,
                limitPrice, plan.getMarket() == Market.KR ? Currency.KRW : Currency.USD);
        orders.submit(exitKey, sell, plan.getExchangeCode(), account);
    }

    private void resumePendingExit(KisOrderIntent plan) {
        UUID exitKey = plan.getRealExitOrderKey();
        if (exitKey == null || plan.getRealExitQuantity() == null || plan.getRealExitLimitPrice() == null
                || plan.getRealExitReason() == null) {
            intents.markRealExitManualReview(plan.getIdempotencyKey());
            return;
        }
        var existing = intents.findById(exitKey);
        if (existing.isPresent()) {
            KisOrderIntent sell = existing.get();
            if (sell.getEnvironment() != KisEnvironment.REAL || sell.getSide() != OrderSide.SELL
                    || sell.getMarket() != plan.getMarket() || !sell.getSymbol().equalsIgnoreCase(plan.getSymbol())
                    || sell.getQuantity() != plan.getRealExitQuantity()
                    || sell.getLimitPrice().compareTo(plan.getRealExitLimitPrice()) != 0) {
                intents.markRealExitManualReview(plan.getIdempotencyKey());
            } else if (sell.getStatus() == KisOrderStatus.FILLED) {
                intents.closeRealExitPlan(plan.getIdempotencyKey());
            } else if (sell.getStatus() == KisOrderStatus.REJECTED || sell.getStatus() == KisOrderStatus.CANCELLED) {
                intents.markRealExitManualReview(plan.getIdempotencyKey());
            } else if (sell.getStatus() == KisOrderStatus.SUBMITTING || sell.getStatus() == KisOrderStatus.UNKNOWN) {
                intents.markRealExitManualReview(plan.getIdempotencyKey());
            }
            return;
        }
        // The claim is durable before POST. If the process stopped before the intent was saved,
        // recreate it with the same persisted key and identical order fields.
        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, plan.getMarket(), null,
                null, plan.getExchangeCode());
        if (!freshRealAccount(account, plan.getMarket(), Instant.now())) return;
        List<KisAccountSnapshot.Position> matching = account.positions().stream()
                .filter(position -> position != null && plan.getSymbol().equalsIgnoreCase(position.symbol())).toList();
        if (matching.size() != 1 || !exactQuantity(matching.getFirst().quantity(), plan.getRealExitQuantity())
                || !exactQuantity(matching.getFirst().sellableQuantity(), plan.getRealExitQuantity())) {
            intents.markRealExitManualReview(plan.getIdempotencyKey());
            return;
        }
        OrderRequest sell = new OrderRequest(plan.getMarket(), plan.getSymbol(), OrderSide.SELL,
                plan.getRealExitQuantity(), plan.getRealExitLimitPrice(),
                plan.getMarket() == Market.KR ? Currency.KRW : Currency.USD);
        orders.submit(exitKey, sell, plan.getExchangeCode(), account);
    }

    private static boolean validBestBid(KisMarketAiEvidenceClient.ExitQuote quote) {
        return quote != null && quote.bestBid() != null && quote.bestBid().signum() > 0
                && quote.price() != null && quote.bestBid().compareTo(quote.price()) <= 0
                && (quote.bestAsk() == null || quote.bestBid().compareTo(quote.bestAsk()) <= 0);
    }

    private static boolean freshRealAccount(KisAccountSnapshot account, Market market, Instant now) {
        return account != null && account.environment() == KisEnvironment.REAL && account.market() == market
                && "KIS_READ_ONLY_REST".equals(account.source()) && account.retrievedAt() != null
                && !account.retrievedAt().isAfter(now)
                && Duration.between(account.retrievedAt(), now).compareTo(MAX_ACCOUNT_AGE) <= 0
                && account.positions() != null;
    }

    private static boolean exactQuantity(BigDecimal actual, int expected) {
        return actual != null && actual.stripTrailingZeros().scale() <= 0
                && actual.compareTo(BigDecimal.valueOf(expected)) == 0;
    }
}
