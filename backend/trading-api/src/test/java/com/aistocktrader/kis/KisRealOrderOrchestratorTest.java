package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.OptionalInt;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRealOrderOrchestratorTest {
    private KisRealOrderClient orders;
    private KisOrderIntentStore intents;
    private KisRealDailyRiskBaselineService dailyRiskBaselines;
    private KisMarketAiEvidenceClient marketAiEvidence;
    private KisRealAccountPortfolioPositionCounter portfolioPositions;
    private KisRealOrderOrchestrator orchestrator;
    private final TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true,
            new BigDecimal("1000000"), new BigDecimal("100"));
    private final KisLiveOrderRiskGate riskGate = new KisLiveOrderRiskGate(safety,
            new KisLiveRiskLimits(BigDecimal.ONE, new BigDecimal("0.25"), BigDecimal.TEN, 3, 3));

    @BeforeEach
    void setUp() {
        orders = mock(KisRealOrderClient.class);
        intents = mock(KisOrderIntentStore.class);
        dailyRiskBaselines = mock(KisRealDailyRiskBaselineService.class);
        marketAiEvidence = mock(KisMarketAiEvidenceClient.class);
        portfolioPositions = mock(KisRealAccountPortfolioPositionCounter.class);
        when(portfolioPositions.countOpenPositionsAcrossMarkets(any())).thenReturn(OptionalInt.of(0));
        when(dailyRiskBaselines.riskInputs(any(), any())).thenReturn(
                new KisRealDailyRiskBaselineService.RiskInputs(true, BigDecimal.ZERO));
        when(marketAiEvidence.inspect(any(), nullable(String.class))).thenReturn(
                new KisMarketAiEvidenceClient.Evidence(true, true, new BigDecimal("100")));
        orchestrator = new KisRealOrderOrchestrator(orders, riskGate, intents, dailyRiskBaselines,
                marketAiEvidence, portfolioPositions, null,
                new KisLiveOrderSizingService(safety,
                        new KisLiveRiskLimits(BigDecimal.ONE, new BigDecimal("0.25"), BigDecimal.TEN, 3, 3)));
    }

    @Test
    void realBuyIntentPersistsServerCappedQuantity() {
        UUID key = UUID.randomUUID();
        OrderRequest proposed = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 100,
                new BigDecimal("10000"), Currency.KRW);
        final KisOrderIntent[] saved = new KisOrderIntent[1];
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), any(OrderRequest.class), isNull()))
                .thenAnswer(call -> {
                    OrderRequest sized = call.getArgument(3);
                    saved[0] = new KisOrderIntent(key, call.getArgument(1), KisEnvironment.REAL, sized, null);
                    return new KisOrderIntentStore.PreparedIntent(saved[0], true);
                });
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> {
            saved[0].reject(call.getArgument(1));
            return saved[0];
        });

        KisOrderIntentResponse response = orchestrator.submit(key, proposed, null, realAccount());

        ArgumentCaptor<OrderRequest> sizedRequest = ArgumentCaptor.forClass(OrderRequest.class);
        verify(intents).prepare(eq(key), anyString(), eq(KisEnvironment.REAL), sizedRequest.capture(), isNull());
        assertEquals(10, sizedRequest.getValue().quantity());
        assertEquals(KisOrderStatus.REJECTED, response.status());
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void missingRealLossHistoryBlocksBuyEvenWhenMarketEvidenceIsApproved() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> { intent.reject(call.getArgument(1)); return intent; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount());

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("CONSECUTIVE_LOSS_COUNT_INVALID", response.responseCode());
        verify(intents).reject(key, "CONSECUTIVE_LOSS_COUNT_INVALID");
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void deniedRiskIsSavedAsRejectedWithoutContactingBroker() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(marketAiEvidence.inspect(eq(request), isNull())).thenReturn(
                new KisMarketAiEvidenceClient.Evidence(false, true, new BigDecimal("100")));
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> { intent.reject(call.getArgument(1)); return intent; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount());

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("APPROVED_MODEL_CANDIDATE_REQUIRED", response.responseCode());
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void unresolvedRealOrderIsReadFromServerStoreBeforeSubmission() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.hasUnresolvedRealOrder(Market.KR, key)).thenReturn(true);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> {
            intent.reject(call.getArgument(1));
            return intent;
        });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount());

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("UNRESOLVED_ORDER_EXISTS", response.responseCode());
        verify(intents).hasUnresolvedRealOrder(Market.KR, key);
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void missingServerDailyBaselineBlocksBuy() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(dailyRiskBaselines.riskInputs(eq(Market.KR), any(KisAccountSnapshot.class))).thenReturn(
                new KisRealDailyRiskBaselineService.RiskInputs(true, null));
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> {
            intent.reject(call.getArgument(1));
            return intent;
        });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount());

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("DAILY_LOSS_BASELINE_REQUIRED", response.responseCode());
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

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount());

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void unavailableConsecutiveLossHistoryIsPersistedAsRejectedWithoutBrokerCall() {
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null);
        when(intents.prepare(eq(key), anyString(), eq(KisEnvironment.REAL), eq(request), isNull()))
                .thenReturn(new KisOrderIntentStore.PreparedIntent(intent, true));
        when(intents.reject(eq(key), anyString())).thenAnswer(call -> { intent.reject(call.getArgument(1)); return intent; });

        KisOrderIntentResponse response = orchestrator.submit(key, request, null, realAccount());

        assertEquals(KisOrderStatus.REJECTED, response.status());
        assertEquals("CONSECUTIVE_LOSS_COUNT_INVALID", response.responseCode());
        verify(intents).reject(key, "CONSECUTIVE_LOSS_COUNT_INVALID");
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void partiallyFilledRealOrderCancellationStoresPendingStateBeforeBrokerCall() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.US, "AAPL", OrderSide.SELL, 4,
                new BigDecimal("10"), Currency.USD);
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, "NASD");
        intent.accept(new KisOrderReceipt("real", "US", "AAPL", "SELL", "9002", null, "0", Instant.now()));
        intent.reconcile(1, 3, new BigDecimal("10"), false, false);
        KisBrokerOrderSnapshot observed = new KisBrokerOrderSnapshot(1, 3, new BigDecimal("10"), false, false);
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(observed));
        when(intents.reconcile(key, observed)).thenAnswer(call -> {
            intent.reconcile(observed.filledQuantity(), observed.remainingQuantity(), observed.averageFillPrice(),
                    observed.cancelled(), observed.rejected());
            return intent;
        });
        when(intents.claimCancelPending(key, KisEnvironment.REAL)).thenAnswer(call -> { intent.markCancelPending(); return true; });
        doAnswer(call -> { intent.markCancelPending(); return null; }).when(orders)
                .cancelRealOrder(Market.US, "AAPL", "9002", null, 3, "NASD");

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.CANCEL_PENDING, response.status());
        var order = inOrder(intents, orders);
        order.verify(orders).reconcileRealOrder(intent);
        order.verify(intents).reconcile(key, observed);
        order.verify(intents).claimCancelPending(key, KisEnvironment.REAL);
        order.verify(orders).cancelRealOrder(Market.US, "AAPL", "9002", null, 3, "NASD");
    }

    @Test
    void uncertainRealCancelIsNeverResentAndCanResolveByReadOnlyReconciliation() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9010", "ORG01", "0", Instant.now()));
        KisBrokerOrderSnapshot open = new KisBrokerOrderSnapshot(0, 1, null, false, false);
        KisBrokerOrderSnapshot cancelled = new KisBrokerOrderSnapshot(0, 0, null, true, false);
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(open), Optional.of(cancelled));
        when(intents.reconcile(key, open)).thenAnswer(call -> {
            intent.reconcile(open.filledQuantity(), open.remainingQuantity(), open.averageFillPrice(),
                    open.cancelled(), open.rejected());
            return intent;
        });
        when(intents.reconcile(key, cancelled)).thenAnswer(call -> {
            intent.reconcile(cancelled.filledQuantity(), cancelled.remainingQuantity(), cancelled.averageFillPrice(),
                    cancelled.cancelled(), cancelled.rejected());
            return intent;
        });
        when(intents.claimCancelPending(key, KisEnvironment.REAL)).thenAnswer(call -> {
            intent.markCancelPending();
            return true;
        });
        doThrow(new KisOrderOutcomeUnknownException("cancel outcome unknown", new java.net.SocketTimeoutException()))
                .when(orders).cancelRealOrder(Market.KR, "005930", "9010", "ORG01", 1, null);

        KisOrderIntentResponse first = orchestrator.cancel(key);
        KisOrderIntentResponse duplicate = orchestrator.cancel(key);
        KisOrderIntentResponse reconciled = orchestrator.reconcile(key);

        assertEquals(KisOrderStatus.CANCEL_PENDING, first.status());
        assertEquals(KisOrderStatus.CANCEL_PENDING, duplicate.status());
        assertEquals(KisOrderStatus.CANCELLED, reconciled.status());
        verify(orders, times(1)).cancelRealOrder(Market.KR, "005930", "9010", "ORG01", 1, null);
        verify(orders, times(2)).reconcileRealOrder(intent);
    }

    @Test
    void definitiveRealCancelRejectionUsesReadOnlyReconciliationAndRestoresOpenOrder() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9004", "ORG01", "0", Instant.now()));
        when(intents.get(key)).thenReturn(intent);
        when(intents.claimCancelPending(key, KisEnvironment.REAL)).thenAnswer(call -> { intent.markCancelPending(); return true; });
        when(intents.recordCancelRejection(key, "EGW00201")).thenAnswer(call -> {
            intent.recordCancelRejection(call.getArgument(1));
            return intent;
        });
        KisBrokerOrderSnapshot open = new KisBrokerOrderSnapshot(0, 1, null, false, false);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(open), Optional.of(open));
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
        verify(orders, times(2)).reconcileRealOrder(intent);
        verify(orders, never()).placeRealLimitOrder(any(), anyString(), any(), anyInt(), any(), any(), anyString(), any());
    }

    @Test
    void missingFreshRealOrderSnapshotBlocksCancelSubmission() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9005", "ORG01", "0", Instant.now()));
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.empty());
        when(intents.markUnknown(key)).thenAnswer(call -> { intent.markUnknown(); return intent; });

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(orders).reconcileRealOrder(intent);
        verify(intents, never()).claimCancelPending(key, KisEnvironment.REAL);
        verify(orders, never()).cancelRealOrder(any(), anyString(), anyString(), anyString(), anyInt(), anyString());
    }

    @Test
    void incompleteBrokerQuantitiesLockRealOrderAsUnknownBeforeCancel() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9007", "ORG01", "0", Instant.now()));
        KisBrokerOrderSnapshot incomplete = new KisBrokerOrderSnapshot(0, 0, null, false, false);
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(incomplete));
        when(intents.markUnknown(key)).thenAnswer(call -> { intent.markUnknown(); return intent; });

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.UNKNOWN, response.status());
        verify(orders).reconcileRealOrder(intent);
        verify(intents).markUnknown(key);
        verify(intents, never()).claimCancelPending(key, KisEnvironment.REAL);
        verify(orders, never()).cancelRealOrder(any(), anyString(), anyString(), anyString(), anyInt(), anyString());
    }

    @Test
    void alreadyFilledRealOrderSnapshotSkipsCancelSubmission() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9006", "ORG01", "0", Instant.now()));
        KisBrokerOrderSnapshot filled = new KisBrokerOrderSnapshot(1, 0, new BigDecimal("70000"), false, false);
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(filled));
        when(intents.reconcile(key, filled)).thenAnswer(call -> {
            intent.reconcile(filled.filledQuantity(), filled.remainingQuantity(), filled.averageFillPrice(),
                    filled.cancelled(), filled.rejected());
            return intent;
        });

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.FILLED, response.status());
        verify(orders).reconcileRealOrder(intent);
        verify(intents, never()).claimCancelPending(key, KisEnvironment.REAL);
        verify(orders, never()).cancelRealOrder(any(), anyString(), anyString(), anyString(), anyInt(), anyString());
    }

    @Test
    void domesticCancelPreflightBlockRestoresObservedStateWithoutAnotherBrokerRead() {
        UUID key = UUID.randomUUID();
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.REAL, buyRequest(), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9007", "ORG01", "0", Instant.now()));
        KisBrokerOrderSnapshot observed = new KisBrokerOrderSnapshot(0, 1, null, false, false);
        when(intents.get(key)).thenReturn(intent);
        when(orders.reconcileRealOrder(intent)).thenReturn(Optional.of(observed));
        when(intents.reconcile(key, observed)).thenAnswer(call -> {
            intent.reconcile(observed.filledQuantity(), observed.remainingQuantity(), observed.averageFillPrice(),
                    observed.cancelled(), observed.rejected());
            return intent;
        });
        when(intents.claimCancelPending(key, KisEnvironment.REAL)).thenAnswer(call -> {
            intent.markCancelPending();
            return true;
        });
        when(intents.recordCancelPreflightBlocked(key, "REAL_CANCEL_PREFLIGHT_BLOCKED")).thenAnswer(call -> {
            intent.recordCancelPreflightBlocked(call.getArgument(1));
            return intent;
        });
        doThrow(new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                "KIS domestic cancel preflight did not match (code REAL_CANCEL_PREFLIGHT_BLOCKED)."))
                .when(orders).cancelRealOrder(Market.KR, "005930", "9007", "ORG01", 1, null);

        KisOrderIntentResponse response = orchestrator.cancel(key);

        assertEquals(KisOrderStatus.ACCEPTED, response.status());
        assertEquals("REAL_CANCEL_PREFLIGHT_BLOCKED", response.responseCode());
        verify(orders).reconcileRealOrder(intent);
        verify(intents).recordCancelPreflightBlocked(key, "REAL_CANCEL_PREFLIGHT_BLOCKED");
        verify(intents, times(2)).reconcile(key, observed);
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
                new BigDecimal("100000"), new BigDecimal("1000000"), List.of(), Instant.now(), "KIS_READ_ONLY_REST");
    }

}
