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
        KisRealOrderReconciliationJob job = new KisRealOrderReconciliationJob(false, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator);

        job.reconcileKnownBrokerOrders();

        verifyNoInteractions(intents, orchestrator);
    }

    @Test
    void missingAccountReadSwitchOrCredentialsKeepsJobIdle() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        new KisRealOrderReconciliationJob(true, kisProperties(true, false, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator)
                .reconcileKnownBrokerOrders();
        new KisRealOrderReconciliationJob(true, kisProperties(true, true, false),
                MarketOperationalScope.domesticOnly(), intents, orchestrator)
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

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator)
                .reconcileKnownBrokerOrders();

        verify(intents).findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyCollection());
        verify(orchestrator).reconcile(key);
        verifyNoMoreInteractions(orchestrator);
    }

    @Test
    void enabledJobRetriesReadOnlyReconciliationForUnknownOrdersOnlyWhenBrokerIdIsKnown() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        UUID knownKey = UUID.randomUUID();
        UUID missingBrokerIdKey = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent unknownWithBrokerId = new KisOrderIntent(knownKey, "hash-known", KisEnvironment.REAL, request, null);
        unknownWithBrokerId.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9002", "ORG01", "0", Instant.now()));
        unknownWithBrokerId.markUnknown();
        KisOrderIntent unknownWithoutBrokerId = new KisOrderIntent(missingBrokerIdKey, "hash-missing", KisEnvironment.REAL, request, null);
        unknownWithoutBrokerId.markUnknown();
        when(intents.findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyCollection()))
                .thenReturn(List.of(unknownWithBrokerId, unknownWithoutBrokerId));

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator)
                .reconcileKnownBrokerOrders();

        verify(intents).findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyCollection());
        verify(orchestrator).reconcile(knownKey);
        verifyNoMoreInteractions(orchestrator);
    }

    @Test
    void domesticOnlyScopeSkipsOutOfScopeOrdersBeforeBrokerReconciliation() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        UUID domesticKey = UUID.randomUUID();
        UUID outOfScopeKey = UUID.randomUUID();
        KisOrderIntent domestic = new KisOrderIntent(domesticKey, "kr-hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW), null);
        domestic.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9003", "ORG01", "0", Instant.now()));
        KisOrderIntent outOfScope = new KisOrderIntent(outOfScopeKey, "other-hash", KisEnvironment.REAL,
                new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 1, new BigDecimal("100"), Currency.USD), null);
        outOfScope.accept(new KisOrderReceipt("real", "US", "AAPL", "BUY", "9004", "ORG01", "0", Instant.now()));
        when(intents.findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyCollection()))
                .thenReturn(List.of(outOfScope, domestic));

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator).reconcileKnownBrokerOrders();

        verify(orchestrator).reconcile(domesticKey);
        verify(orchestrator, never()).reconcile(outOfScopeKey);
    }

    private static KisProperties kisProperties(boolean globalRead, boolean realRead, boolean credentials) {
        return new KisProperties(globalRead, false, realRead, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account(credentials ? "real-key" : "", credentials ? "real-secret" : "",
                        credentials ? "8765432101" : ""),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }
}
