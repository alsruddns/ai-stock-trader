package com.aistocktrader.kis;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;

/** Optional read-only polling for known REAL broker orders; unknown orders without broker IDs remain manual-only. */
@Component
public class KisRealOrderReconciliationJob {
    private static final Logger log = LoggerFactory.getLogger(KisRealOrderReconciliationJob.class);
    private static final List<KisOrderStatus> POLLABLE_STATUSES =
            List.of(KisOrderStatus.ACCEPTED, KisOrderStatus.PARTIALLY_FILLED,
                    KisOrderStatus.CANCEL_PENDING, KisOrderStatus.UNKNOWN);

    private final boolean enabled;
    private final KisProperties properties;
    private final KisOrderIntentRepository intents;
    private final KisRealOrderOrchestrator orchestrator;

    public KisRealOrderReconciliationJob(
            @Value("${kis.real-reconciliation-enabled:false}") boolean enabled,
            KisProperties properties, KisOrderIntentRepository intents,
            KisRealOrderOrchestrator orchestrator) {
        this.enabled = enabled;
        this.properties = properties;
        this.intents = intents;
        this.orchestrator = orchestrator;
    }

    @Scheduled(fixedDelayString = "${kis.real-reconciliation-delay-ms:30000}")
    public void reconcileKnownBrokerOrders() {
        if (!enabled || !properties.accountReadEnabled() || !properties.realAccountReadEnabled()
                || !properties.credentials(KisEnvironment.REAL).configured()) return;
        intents.findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(KisEnvironment.REAL, POLLABLE_STATUSES)
                .stream().filter(intent -> intent.getBrokerOrderNumber() != null
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
