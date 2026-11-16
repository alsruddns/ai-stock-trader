package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

/** Domestic PAPER protective exits, disabled until separately verified and enabled. */
@Component
public class KisPaperProtectiveExitMonitor {
    private static final Logger log = LoggerFactory.getLogger(KisPaperProtectiveExitMonitor.class);
    private static final Duration MAX_ACCOUNT_AGE = Duration.ofSeconds(5);
    private static final Duration MAX_SESSION_AGE = Duration.ofSeconds(30);
    private static final Duration FLATTEN_BEFORE_CLOSE = Duration.ofMinutes(5);
    private static final List<KisPaperExitStatus> MONITORED =
            List.of(KisPaperExitStatus.ARMED, KisPaperExitStatus.EXIT_PENDING);
    private static final int PAGE_SIZE = 50;

    private final boolean automationEnabled;
    private final boolean exitPathVerified;
    private final boolean cancelPreflightVerified;
    private final KisOrderProperties orderProperties;
    private final KisProperties kis;
    private final TradingSafetyProperties safety;
    private final MarketOperationalScope marketScope;
    private final KisPaperExitPlanRepository plans;
    private final KisOrderIntentRepository intents;
    private final KisAccountClient accounts;
    private final KisMarketSessionClient sessions;
    private final KisMarketAiEvidenceClient marketData;
    private final KisOrderOrchestrator orders;
    private final KisRealExitPolicy policy = new KisRealExitPolicy(Duration.ofSeconds(3));
    private final AtomicInteger nextPage = new AtomicInteger();

    public KisPaperProtectiveExitMonitor(
            @Value("${kis.orders.paper-exit-monitor-enabled:false}") boolean automationEnabled,
            @Value("${kis.orders.paper-protective-exit-verified:false}") boolean exitPathVerified,
            @Value("${kis.orders.paper-cancel-preflight-verified:false}") boolean cancelPreflightVerified,
            KisOrderProperties orderProperties, KisProperties kis, TradingSafetyProperties safety,
            MarketOperationalScope marketScope, KisPaperExitPlanRepository plans,
            KisOrderIntentRepository intents, KisAccountClient accounts, KisMarketSessionClient sessions,
            KisMarketAiEvidenceClient marketData, KisOrderOrchestrator orders) {
        this.automationEnabled = automationEnabled;
        this.exitPathVerified = exitPathVerified;
        this.cancelPreflightVerified = cancelPreflightVerified;
        this.orderProperties = orderProperties;
        this.kis = kis;
        this.safety = safety;
        this.marketScope = marketScope;
        this.plans = plans;
        this.intents = intents;
        this.accounts = accounts;
        this.sessions = sessions;
        this.marketData = marketData;
        this.orders = orders;
    }

    @Scheduled(fixedDelayString = "${kis.orders.paper-exit-monitor-poll-ms:1000}")
    public void monitorDomesticPaperExits() {
        if (!enabled()) return;
        try {
            int pageNumber = nextPage.get();
            Page<KisPaperExitPlan> page = plans.findByStatusInOrderByUpdatedAtAsc(
                    MONITORED, PageRequest.of(pageNumber, PAGE_SIZE));
            nextPage.set(page.getTotalPages() == 0 || pageNumber + 1 >= page.getTotalPages() ? 0 : pageNumber + 1);
            page.getContent().forEach(this::processSafely);
        } catch (RuntimeException exception) {
            log.warn("KIS PAPER protective-exit scan failed ({})", exception.getClass().getSimpleName());
        }
    }

    private boolean enabled() {
        return automationEnabled && exitPathVerified && cancelPreflightVerified && orderProperties.paperEnabled()
                && orderProperties.paperReconciliationEnabled() && kis.accountReadEnabled()
                && kis.paperAccountReadEnabled() && kis.credentials(KisEnvironment.PAPER).configured()
                && !safety.liveTradingEnabled() && "paper".equalsIgnoreCase(safety.mode())
                && marketScope.allowedMarkets().contains(Market.KR);
    }

    private void processSafely(KisPaperExitPlan plan) {
        try {
            KisOrderIntent entry = intents.findById(plan.getEntryIdempotencyKey()).orElse(null);
            if (entry == null || entry.getEnvironment() != KisEnvironment.PAPER
                    || entry.getMarket() != Market.KR || entry.getSide() != OrderSide.BUY
                    || entry.getExitStopDistancePerShare() == null
                    || entry.getExitRewardRiskMultiple() == null) {
                plans.requireManualReview(plan.getEntryIdempotencyKey());
                return;
            }
            if (plan.getStatus() == KisPaperExitStatus.EXIT_PENDING) {
                resumePendingExit(plan, entry);
            } else if (plan.getStatus() == KisPaperExitStatus.ARMED) {
                evaluateArmedPlan(plan, entry);
            }
        } catch (RuntimeException exception) {
            log.warn("KIS PAPER protective-exit check failed for KR {} ({})",
                    plan.getEntryIdempotencyKey(), exception.getClass().getSimpleName());
        }
    }

    private void evaluateArmedPlan(KisPaperExitPlan plan, KisOrderIntent entry) {
        if (!protectedFilledEntry(entry)) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }
        if (plans.countActiveForSymbol(KisEnvironment.PAPER.name(), Market.KR.name(), entry.getSymbol()) != 1) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }
        Instant now = Instant.now();
        KisMarketSessionClient.MarketSession session = sessions.session(Market.KR);
        if (!freshSession(session, now) || !session.session() || !session.open()
                || now.isBefore(session.openAt()) || !now.isBefore(session.closeAt())) return;

        KisMarketAiEvidenceClient.ExitQuote quote = marketData.quoteForExit(
                KisEnvironment.PAPER, Market.KR, entry.getSymbol(), null);
        Instant decisionAt = Instant.now();
        KisRealExitPolicy.Decision decision = policy.evaluate(new KisRealExitPolicy.Input(
                entry.getFilledQuantity() == null ? 0 : entry.getFilledQuantity(), entry.getAverageFillPrice(),
                entry.getExitStopPrice(), entry.getExitTargetPrice(), quote.price(), quote.retrievedAt(),
                decisionAt, session.closeAt().minus(FLATTEN_BEFORE_CLOSE)));
        if (decision != KisRealExitPolicy.Decision.STOP_LOSS
                && decision != KisRealExitPolicy.Decision.TAKE_PROFIT
                && decision != KisRealExitPolicy.Decision.SESSION_END) return;
        if (!validBestBid(quote)) return;

        int quantity = entry.getFilledQuantity() == null ? 0 : entry.getFilledQuantity();
        if (quantity <= 0 || entry.getRemainingQuantity() == null || entry.getRemainingQuantity() != 0) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }
        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.PAPER, Market.KR, null, null, "");
        if (!freshPaperAccount(account, Instant.now())) return;
        if (!exactlySellable(account, entry.getSymbol(), quantity)) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }

        UUID exitKey = UUID.randomUUID();
        BigDecimal limitPrice = quote.bestBid();
        if (plans.claimExit(plan.getEntryIdempotencyKey(), exitKey, decision.name(), quantity, limitPrice) != 1) return;
        submitExit(exitKey, entry, quantity, limitPrice);
    }

    private void resumePendingExit(KisPaperExitPlan plan, KisOrderIntent entry) {
        if (!protectedFilledEntry(entry)) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }
        UUID exitKey = plan.getExitOrderKey();
        Integer quantity = plan.getExitQuantity();
        BigDecimal limitPrice = plan.getExitLimitPrice();
        if (exitKey == null || quantity == null || quantity <= 0 || limitPrice == null || limitPrice.signum() <= 0
                || plan.getExitReason() == null) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }
        KisOrderIntent exit = intents.findById(exitKey).orElse(null);
        if (exit != null) {
            if (exit.getEnvironment() != KisEnvironment.PAPER || exit.getSide() != OrderSide.SELL
                    || exit.getMarket() != Market.KR || !exit.getSymbol().equalsIgnoreCase(entry.getSymbol())
                    || exit.getQuantity() != quantity || exit.getLimitPrice().compareTo(limitPrice) != 0) {
                plans.requireManualReview(plan.getEntryIdempotencyKey());
            }
            return;
        }

        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.PAPER, Market.KR, null, null, "");
        if (!freshPaperAccount(account, Instant.now())) return;
        if (!exactlySellable(account, entry.getSymbol(), quantity)) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return;
        }
        submitExit(exitKey, entry, quantity, limitPrice);
    }

    private void submitExit(UUID exitKey, KisOrderIntent entry, int quantity, BigDecimal limitPrice) {
        OrderRequest sell = new OrderRequest(Market.KR, entry.getSymbol(), OrderSide.SELL, quantity,
                limitPrice, Currency.KRW);
        orders.submitPaperOrder(exitKey, sell, null);
    }

    private static boolean freshSession(KisMarketSessionClient.MarketSession session, Instant now) {
        return session != null && session.market() == Market.KR && session.evaluatedAt() != null
                && !session.evaluatedAt().isAfter(now)
                && Duration.between(session.evaluatedAt(), now).compareTo(MAX_SESSION_AGE) <= 0
                && session.sessionDate() != null && session.openAt() != null && session.closeAt() != null
                && session.closeAt().isAfter(session.openAt());
    }

    private static boolean protectedFilledEntry(KisOrderIntent entry) {
        return entry.getFilledQuantity() != null && entry.getFilledQuantity() > 0
                && entry.getRemainingQuantity() != null && entry.getRemainingQuantity() == 0
                && (entry.getStatus() == KisOrderStatus.FILLED || entry.getStatus() == KisOrderStatus.CANCELLED);
    }

    private static boolean freshPaperAccount(KisAccountSnapshot account, Instant now) {
        return account != null && account.environment() == KisEnvironment.PAPER && account.market() == Market.KR
                && "KIS_READ_ONLY_REST".equals(account.source()) && account.retrievedAt() != null
                && !account.retrievedAt().isAfter(now)
                && Duration.between(account.retrievedAt(), now).compareTo(MAX_ACCOUNT_AGE) <= 0
                && account.positions() != null;
    }

    private static boolean exactlySellable(KisAccountSnapshot account, String symbol, int quantity) {
        List<KisAccountSnapshot.Position> matching = account.positions().stream()
                .filter(position -> position != null && symbol.equalsIgnoreCase(position.symbol())).toList();
        return matching.size() == 1 && exactQuantity(matching.getFirst().quantity(), quantity)
                && exactQuantity(matching.getFirst().sellableQuantity(), quantity);
    }

    private static boolean exactQuantity(BigDecimal actual, int expected) {
        return actual != null && actual.stripTrailingZeros().scale() <= 0
                && actual.compareTo(BigDecimal.valueOf(expected)) == 0;
    }

    private static boolean validBestBid(KisMarketAiEvidenceClient.ExitQuote quote) {
        return quote != null && quote.market() == Market.KR && quote.bestBid() != null
                && quote.bestBid().signum() > 0 && quote.price() != null
                && quote.bestBid().compareTo(quote.price()) <= 0
                && (quote.bestAsk() == null || quote.bestBid().compareTo(quote.bestAsk()) <= 0);
    }
}
