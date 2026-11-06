package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRealOrderOrchestratorTest {
    private KisRealOrderClient orders;
    private KisOrderIntentStore intents;
    private KisRealOrderOrchestrator orchestrator;
    private final TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true,
            new BigDecimal("1000000"), new BigDecimal("100"));
    private final KisLiveOrderRiskGate riskGate = new KisLiveOrderRiskGate(safety,
            new KisLiveRiskLimits(BigDecimal.ONE, new BigDecimal("0.25"), BigDecimal.TEN, 3, 3));

    @BeforeEach
    void setUp() {
        orders = mock(KisRealOrderClient.class);
        intents = mock(KisOrderIntentStore.class);
        orchestrator = new KisRealOrderOrchestrator(orders, riskGate, intents);
    }

    @Test
    void approvedOrderIsPersistedAsRealBeforeItIsSentAndReceiptIsStored() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        KisOrderReceipt receipt = new KisOrderReceipt("real", "KR", "005930", "BUY", "9001", "ORG01", "0", Instant.now());
        when(orders.placeRealLimitOrder(eq(Market.KR), eq("005930"), eq(OrderSide.BUY), eq(1),
                eq(new BigDecimal("70000")), eq(Currency.KRW), isNull(), any(KisLiveRiskDecision.class)))
                .thenReturn(receipt);
        when(intents.accept(key, receipt)).thenAnswer(call -> { intent.accept(receipt); return intent; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount(), safeBuyContext());

        assertEquals("real", response.environment());
        assertEquals(KisOrderStatus.ACCEPTED, response.status());
        assertEquals("9001", response.brokerOrderNumber());
        var order = inOrder(intents, orders);
        order.verify(intents).prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull());
        order.verify(orders).placeRealLimitOrder(eq(Market.KR), eq("005930"), eq(OrderSide.BUY), eq(1),
                eq(new BigDecimal("70000")), eq(Currency.KRW), isNull(), any(KisLiveRiskDecision.class));
    }

    @Test
    void deniedRiskIsSavedAsRejectedWithoutContactingBroker() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> { intent.reject(call.getArgument(1)); return intent; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount(),
                new KisLiveRiskContext(false, true, true, true, false, 0, BigDecimal.ZERO, 0,
                        new BigDecimal("1000000"), new BigDecimal("100")));

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("APPROVED_MODEL_CANDIDATE_REQUIRED", response.responseCode());
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void duplicateSubmittingIntentBecomesUnknownAndIsNeverResent() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent existing = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(existing, false));
        when(intents.markUnknown(key)).thenAnswer(call -> { existing.markUnknown(); return existing; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount(), safeBuyContext());

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void unknownBrokerResultIsStoredAndNotRetried() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(orders.placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), any(), any()))
                .thenThrow(new KisOrderOutcomeUnknownException("unknown", new RuntimeException("timeout")));
        when(intents.markUnknown(key)).thenAnswer(call -> { intent.markUnknown(); return intent; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount(), safeBuyContext());

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(intents).markUnknown(key);
    }

    @Test
    void partiallyFilledRealOrderCancellationStoresPendingStateBeforeBrokerCall() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.US, "AAPL", OrderSide.SELL, 4,
                new BigDecimal("10"), Currency.USD);
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, "NASD");
        intent.accept(new KisOrderReceipt("real", "US", "AAPL", "SELL", "9002", null, "0", Instant.now()));
        intent.reconcile(1, 3, new BigDecimal("10"), false, false);
        when(intents.get(key)).thenReturn(intent);
        when(intents.markCancelPending(key)).thenAnswer(call -> { intent.markCancelPending(); return intent; });
        doAnswer(call -> { intent.markCancelPending(); return null; }).when(orders)
                .cancelRealOrder(Market.US, "AAPL", "9002", null, 3, "NASD");

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.CANCEL_PENDING, response.status());
        var order = inOrder(intents, orders);
        order.verify(intents).markCancelPending(key);
        order.verify(orders).cancelRealOrder(Market.US, "AAPL", "9002", null, 3, "NASD");
    }

    @Test
    void definitiveRealCancelRejectionUsesReadOnlyReconciliationAndRestoresOpenOrder() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9004", "ORG01", "0", Instant.now()));
        when(intents.get(key)).thenReturn(intent);
        when(intents.markCancelPending(key)).thenAnswer(call -> { intent.markCancelPending(); return intent; });
        when(intents.recordCancelRejection(key, "EGW00201")).thenAnswer(call -> {
            intent.recordCancelRejection(call.getArgument(1));
            return intent;
        });
        KisBrokerOrderSnapshot open = new KisBrokerOrderSnapshot(0, 1, null, false, false);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(open));
        when(intents.reconcile(key, open)).thenAnswer(call -> {
            intent.reconcile(open.filledQuantity(), open.remainingQuantity(), open.averageFillPrice(),
                    open.cancelled(), open.rejected());
            return intent;
        });
        doThrow(new KisApiException(HttpStatus.BAD_GATEWAY, "KIS rejected the real order request (code EGW00201)."))
                .when(orders).cancelRealOrder(Market.KR, "005930", "9004", "ORG01", 1, null);

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.ACCEPTED, response.status());
        assertEquals("EGW00201", response.responseCode());
        assertEquals(1, response.remainingQuantity());
        verify(orders).reconcileRealOrder(intent);
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void realOrderReconciliationPersistsBrokerFillState() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9003", "ORG01", "0", Instant.now()));
        KisBrokerOrderSnapshot snapshot = new KisBrokerOrderSnapshot(1, 0, new BigDecimal("70100"), false, false);
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(snapshot));
        when(intents.reconcile(key, snapshot)).thenAnswer(call -> {
            intent.reconcile(snapshot.filledQuantity(), snapshot.remainingQuantity(), snapshot.averageFillPrice(),
                    snapshot.cancelled(), snapshot.rejected());
            return intent;
        });

        KisOrderIntentResponse response = orchestrator.reconcile(key);

        assertEquals(KisOrderStatus.FILLED, response.status());
        assertEquals(1, response.filledQuantity());
        assertEquals("70100", response.averageFillPrice());
        verify(orders).reconcileRealOrder(intent);
    }

    @Test
    void reconciliationRefusesToUsePaperIntent() {
        UUID key = UUID.randomUUID();
        KisOrderIntent paperIntent = new KisOrderIntent(key, "hash", KisEnvironment.PAPER, buyRequest(), null);
        when(intents.get(key)).thenReturn(paperIntent);

        org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class, () -> orchestrator.reconcile(key));

        verify(orders, never()).reconcileRealOrder(any());
        verify(intents, never()).reconcile(any(), any());
    }

    private static OrderRequest buyRequest() {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW);
    }

    private static KisAccountSnapshot realAccount() {
        return new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("1000000"),
                new BigDecimal("100000"), List.of(), Instant.now(), "TEST");
    }

    private static KisLiveRiskContext safeBuyContext() {
        return new KisLiveRiskContext(true, true, true, true, false, 0, BigDecimal.ZERO, 0,
                new BigDecimal("1000000"), new BigDecimal("100"));
    }
}
