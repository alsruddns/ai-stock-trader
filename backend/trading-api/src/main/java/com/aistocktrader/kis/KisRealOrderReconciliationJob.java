package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/** Optional read-only polling for known REAL broker orders; unknown orders without broker IDs remain manual-only. */
@Component
public class KisRealOrderReconciliationJob {
    private static final Logger log = LoggerFactory.getLogger(KisRealOrderReconciliationJob.class);
    private static final List<KisOrderStatus> POLLABLE_STATUSES =
            List.of(KisOrderStatus.ACCEPTED, KisOrderStatus.PARTIALLY_FILLED,
                    KisOrderStatus.CANCEL_PENDING, KisOrderStatus.UNKNOWN);
    private static final int PAGE_SIZE = 50;

    private final boolean enabled;
    private final KisProperties properties;
    private final MarketOperationalScope marketScope;
    private final KisOrderIntentRepository intents;
    private final KisRealOrderOrchestrator orchestrator;
    private final AtomicInteger nextPage = new AtomicInteger();

    public KisRealOrderReconciliationJob(
            @Value("${kis.real-reconciliation-enabled:false}") boolean enabled,
            KisProperties properties, MarketOperationalScope marketScope,
            KisOrderIntentRepository intents,
            KisRealOrderOrchestrator orchestrator) {
        this.enabled = enabled;
        this.properties = properties;
        this.marketScope = marketScope;
        this.intents = intents;
        this.orchestrator = orchestrator;
    }

    @Scheduled(fixedDelayString = "${kis.real-reconciliation-delay-ms:30000}")
    public void reconcileKnownBrokerOrders() {
        if (!enabled || !properties.accountReadEnabled() || !properties.realAccountReadEnabled()
                || !properties.credentials(KisEnvironment.REAL).configured()) return;
        List<Market> allowedMarkets = marketScope.allowedMarkets().stream().sorted().toList();
        if (allowedMarkets.isEmpty()) return;
        int pageNumber = nextPage.get();
        Page<KisOrderIntent> page = intents.findReconciliationCandidates(
                KisEnvironment.REAL, allowedMarkets, POLLABLE_STATUSES, PageRequest.of(pageNumber, PAGE_SIZE));
        nextPage.set(page.getTotalPages() == 0 || pageNumber + 1 >= page.getTotalPages() ? 0 : pageNumber + 1);
        page.getContent().stream().filter(intent -> allowedMarkets.contains(intent.getMarket()))
                .filter(intent -> intent.getBrokerOrderNumber() != null
                        && !intent.getBrokerOrderNumber().isBlank())
                .forEach(intent -> {
                    try {
                        orchestrator.reconcile(intent.getIdempotencyKey());
                    } catch (RuntimeException exception) {
                        log.warn("KIS REAL order status lookup failed for {} {} ({})",
                                intent.getMarket(), intent.getSymbol(), exception.getClass().getSimpleName());
                    }
                });
    }
}
