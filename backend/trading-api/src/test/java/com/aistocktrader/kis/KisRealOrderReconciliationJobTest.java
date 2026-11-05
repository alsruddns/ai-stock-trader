package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class KisRealOrderReconciliationJobTest {
    @Test
    void disabledJobDoesNotQueryDatabaseOrBroker() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        KisRealOrderReconciliationJob job = new KisRealOrderReconciliationJob(false, kisProperties(true, true, true), intents, orchestrator);

        job.reconcileKnownBrokerOrders();

        verifyNoInteractions(intents, orchestrator);
    }

    @Test
    void missingAccountReadSwitchOrCredentialsKeepsJobIdle() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        new KisRealOrderReconciliationJob(true, kisProperties(true, false, true), intents, orchestrator)
                .reconcileKnownBrokerOrders();
        new KisRealOrderReconciliationJob(true, kisProperties(true, true, false), intents, orchestrator)
                .reconcileKnownBrokerOrders();

        verifyNoInteractions(intents, orchestrator);
    }

    @Test
    void enabledJobOnlyPassesKnownBrokerOrdersFromRealOpenStatusesToReadOnlyReconciler() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        UUID key = UUID.randomUUID();
        KisOrderIntent known = new KisOrderIntent(key, "hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW), null);
        known.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9001", "ORG01", "0", Instant.now()));
        when(intents.findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyCollection()))
                .thenReturn(List.of(known));

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true), intents, orchestrator)
                .reconcileKnownBrokerOrders();

        verify(intents).findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyCollection());
        verify(orchestrator).reconcile(key);
        verifyNoMoreInteractions(orchestrator);
    }

    private static KisProperties kisProperties(boolean globalRead, boolean realRead, boolean credentials) {
        return new KisProperties(globalRead, false, realRead, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account(credentials ? "real-key" : "", credentials ? "real-secret" : "",
                        credentials ? "8765432101" : ""),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }
}
