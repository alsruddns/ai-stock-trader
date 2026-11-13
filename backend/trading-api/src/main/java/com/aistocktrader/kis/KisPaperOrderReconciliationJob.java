package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Market;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/** Optional read-only polling for known domestic PAPER broker orders. */
@Component
public class KisPaperOrderReconciliationJob {
    private static final Logger log = LoggerFactory.getLogger(KisPaperOrderReconciliationJob.class);
    private static final List<KisOrderStatus> POLLABLE_STATUSES =
            List.of(KisOrderStatus.ACCEPTED, KisOrderStatus.PARTIALLY_FILLED,
                    KisOrderStatus.CANCEL_PENDING, KisOrderStatus.UNKNOWN);
    private static final int PAGE_SIZE = 50;

    private final KisOrderProperties orderProperties;
    private final KisProperties properties;
    private final TradingSafetyProperties safety;
    private final MarketOperationalScope marketScope;
    private final KisOrderIntentRepository intents;
    private final KisOrderOrchestrator orchestrator;
    private final AtomicInteger nextPage = new AtomicInteger();

    public KisPaperOrderReconciliationJob(KisOrderProperties orderProperties, KisProperties properties,
                                          TradingSafetyProperties safety, MarketOperationalScope marketScope,
                                          KisOrderIntentRepository intents, KisOrderOrchestrator orchestrator) {
        this.orderProperties = orderProperties;
        this.properties = properties;
        this.safety = safety;
        this.marketScope = marketScope;
        this.intents = intents;
        this.orchestrator = orchestrator;
    }

    @Scheduled(fixedDelayString = "${kis.orders.paper-reconciliation-delay-ms:30000}")
    public void reconcileKnownBrokerOrders() {
        if (!orderProperties.paperReconciliationEnabled()
                || !properties.accountReadEnabled() || !properties.paperAccountReadEnabled()
                || safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())
                || !properties.credentials(KisEnvironment.PAPER).configured()) return;

        List<Market> allowedMarkets = marketScope.allowedMarkets().stream().sorted().toList();
        if (allowedMarkets.isEmpty()) return;
        int pageNumber = nextPage.get();
        Page<KisOrderIntent> page = intents.findByEnvironmentAndMarketInAndStatusInOrderByUpdatedAtAscIdempotencyKeyAsc(
                KisEnvironment.PAPER, allowedMarkets, POLLABLE_STATUSES,
                PageRequest.of(pageNumber, PAGE_SIZE));
        nextPage.set(page.getTotalPages() == 0 || pageNumber + 1 >= page.getTotalPages() ? 0 : pageNumber + 1);
        page.getContent().stream().filter(intent -> allowedMarkets.contains(intent.getMarket()))
                .filter(intent -> intent.getBrokerOrderNumber() != null
                        && !intent.getBrokerOrderNumber().isBlank())
                .forEach(intent -> {
                    try {
                        orchestrator.reconcilePaperOrder(intent.getIdempotencyKey());
                    } catch (RuntimeException exception) {
                        log.warn("KIS PAPER order status lookup failed for {} {} ({})",
                                intent.getMarket(), intent.getSymbol(), exception.getClass().getSimpleName());
                    }
                });
    }
}
