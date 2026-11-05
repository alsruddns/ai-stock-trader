package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataIntegrityViolationException;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.nullable;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class KisOrderOrchestratorTest {
    private KisOrderClient orders;
    private KisAccountClient accounts;
    private KisOrderIntentStore intents;
    private KisOrderOrchestrator orchestrator;

    @BeforeEach
    void setUp() {
        orders = mock(KisOrderClient.class);
        accounts = mock(KisAccountClient.class);
        intents = mock(KisOrderIntentStore.class);
        orchestrator = new KisOrderOrchestrator(orders, accounts, new KisOrderProperties(true),
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100")), intents);
    }

    @Test
    void duplicateRequestReturnsExistingIntentWithoutSendingAgain() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent existing = new KisOrderIntent(key, "same-hash", request, null);
        existing.markUnknown();
        when(intents.prepare(any(), anyString(), any(), nullable(String.class))).thenReturn(new KisOrderIntentStore.PreparedIntent(existing, false));

        KisOrderIntentResponse response = orchestrator.submitPaperOrder(key, request, null);

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(accounts, never()).getBalance(any(), any(), any(), any(), anyString());
        verify(orders, never()).placePaperLimitOrder(any(), anyString(), any(), org.mockito.ArgumentMatchers.anyInt(), any(), any(), anyString());
    }

    @Test
    void insufficientCashRejectsBeforeBrokerOrderCall() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", request, null);
        when(intents.prepare(any(), anyString(), any(), nullable(String.class))).thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(accounts.getBalance(any(), any(), any(), any(), anyString())).thenReturn(
                new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW", BigDecimal.ZERO,
                        new BigDecimal("50000"), List.of(), Instant.now(), "test"));
        when(intents.reject(key, "PREFLIGHT_BLOCKED")).thenAnswer(invocation -> {
            intent.reject("PREFLIGHT_BLOCKED");
            return intent;
        });

        KisOrderIntentResponse response = orchestrator.submitPaperOrder(key, request, null);

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("PREFLIGHT_BLOCKED", response.responseCode());
        verify(orders, never()).placePaperLimitOrder(any(), anyString(), any(), org.mockito.ArgumentMatchers.anyInt(), any(), any(), anyString());
    }

    @Test
    void uncertainBrokerOutcomeIsPersistedAndNeverRetriedAutomatically() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", request, null);
        when(intents.prepare(any(), anyString(), any(), nullable(String.class))).thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(accounts.getBalance(any(), any(), any(), any(), anyString())).thenReturn(
                new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW", BigDecimal.ZERO,
                        new BigDecimal("100000"), List.of(), Instant.now(), "test"));
        when(orders.placePaperLimitOrder(any(), anyString(), any(), org.mockito.ArgumentMatchers.anyInt(), any(), any(), nullable(String.class)))
                .thenThrow(new KisOrderOutcomeUnknownException("unknown", new RuntimeException("timeout")));
        intent.markUnknown();
        doReturn(intent).when(intents).markUnknown(key);

        KisOrderIntentResponse response = orchestrator.submitPaperOrder(key, request, null);

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(intents).markUnknown(key);
    }

    @Test
    void databaseDuplicateRaceReadsExistingIntentWithoutBrokerSubmission() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent existing = new KisOrderIntent(key, "hash", request, null);
        existing.markUnknown();
        when(intents.prepare(any(), anyString(), any(), nullable(String.class))).thenThrow(new DataIntegrityViolationException("duplicate"));
        when(intents.get(key)).thenReturn(existing);

        KisOrderIntentResponse response = orchestrator.submitPaperOrder(key, request, null);

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(orders, never()).placePaperLimitOrder(any(), anyString(), any(), org.mockito.ArgumentMatchers.anyInt(), any(), any(), anyString());
    }

    @Test
    void disabledOrdersFailClosed() {
        KisOrderOrchestrator locked = new KisOrderOrchestrator(orders, accounts, new KisOrderProperties(false),
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100")), intents);
        assertThrows(IllegalStateException.class, () -> locked.submitPaperOrder(UUID.randomUUID(), buyRequest(), null));
        verify(intents, never()).prepare(any(), anyString(), any(), nullable(String.class));
    }

    private static OrderRequest buyRequest() {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW);
    }
}
