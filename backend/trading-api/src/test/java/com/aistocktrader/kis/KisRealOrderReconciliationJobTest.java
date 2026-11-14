package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.*;
import org.mockito.ArgumentCaptor;

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
        when(intents.findReconciliationCandidates(eq(KisEnvironment.REAL), anyCollection(), anyCollection(),
                any(Pageable.class))).thenReturn(new PageImpl<>(List.of(known)));

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator)
                .reconcileKnownBrokerOrders();

        verify(intents).findReconciliationCandidates(eq(KisEnvironment.REAL), eq(List.of(Market.KR)),
                anyCollection(), any(Pageable.class));
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
        when(intents.findReconciliationCandidates(eq(KisEnvironment.REAL), anyCollection(), anyCollection(),
                any(Pageable.class))).thenReturn(new PageImpl<>(List.of(unknownWithBrokerId, unknownWithoutBrokerId)));

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator)
                .reconcileKnownBrokerOrders();

        verify(intents).findReconciliationCandidates(eq(KisEnvironment.REAL), eq(List.of(Market.KR)),
                anyCollection(), any(Pageable.class));
        verify(orchestrator).reconcile(knownKey);
        verifyNoMoreInteractions(orchestrator);
    }

    @Test
    void domesticOnlyScopePagesOnlyDomesticOrdersBeforeBrokerReconciliation() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        UUID domesticKey = UUID.randomUUID();
        KisOrderIntent domestic = new KisOrderIntent(domesticKey, "kr-hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW), null);
        domestic.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9003", "ORG01", "0", Instant.now()));
        when(intents.findReconciliationCandidates(eq(KisEnvironment.REAL), anyCollection(), anyCollection(),
                any(Pageable.class))).thenReturn(new PageImpl<>(List.of(domestic)));

        new KisRealOrderReconciliationJob(true, kisProperties(true, true, true),
                MarketOperationalScope.domesticOnly(), intents, orchestrator).reconcileKnownBrokerOrders();

        verify(intents).findReconciliationCandidates(eq(KisEnvironment.REAL), eq(List.of(Market.KR)),
                anyCollection(), any(Pageable.class));
        verify(orchestrator).reconcile(domesticKey);
    }

    @Test
    void enabledJobRotatesAcrossDomesticCandidatePages() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisRealOrderOrchestrator orchestrator = mock(KisRealOrderOrchestrator.class);
        when(intents.findReconciliationCandidates(eq(KisEnvironment.REAL), anyCollection(), anyCollection(),
                any(Pageable.class))).thenAnswer(invocation -> {
            Pageable pageable = invocation.getArgument(3);
            return new PageImpl<KisOrderIntent>(List.of(), pageable, 120);
        });
        KisRealOrderReconciliationJob job = new KisRealOrderReconciliationJob(true,
                kisProperties(true, true, true), MarketOperationalScope.domesticOnly(), intents, orchestrator);

        job.reconcileKnownBrokerOrders();
        job.reconcileKnownBrokerOrders();
        job.reconcileKnownBrokerOrders();
        job.reconcileKnownBrokerOrders();

        ArgumentCaptor<Pageable> pageCaptor = ArgumentCaptor.forClass(Pageable.class);
        verify(intents, times(4)).findReconciliationCandidates(eq(KisEnvironment.REAL), eq(List.of(Market.KR)),
                anyCollection(), pageCaptor.capture());
        assertEquals(List.of(0, 1, 2, 0), pageCaptor.getAllValues().stream().map(Pageable::getPageNumber).toList());
        verifyNoInteractions(orchestrator);
    }

    private static KisProperties kisProperties(boolean globalRead, boolean realRead, boolean credentials) {
        return new KisProperties(globalRead, false, realRead, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account(credentials ? "real-key" : "", credentials ? "real-secret" : "",
                        credentials ? "8765432101" : ""),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }
}
