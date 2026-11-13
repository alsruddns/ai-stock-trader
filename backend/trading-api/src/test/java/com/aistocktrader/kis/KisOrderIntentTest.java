package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class KisOrderIntentTest {
    @Test
    void newCancelAttemptClearsPriorRejectionAndKeepsConfirmedPendingState() {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        intent.markCancelPending();
        intent.recordCancelRejection("EGW00201");
        intent.reconcile(0, 1, null, false, false);
        assertEquals(KisOrderStatus.ACCEPTED, intent.getStatus());
        assertEquals("EGW00201", intent.getBrokerResponseCode());

        intent.markCancelPending();
        intent.reconcile(0, 1, null, false, false);
        assertEquals(KisOrderStatus.CANCEL_PENDING, intent.getStatus());
        assertEquals("0", intent.getBrokerResponseCode());
    }

    @Test
    void domesticReceiptWithoutBrokerOrganizationStaysUnknown() {
        KisOrderIntent domestic = new KisOrderIntent(UUID.randomUUID(), "hash",
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("70000"), Currency.KRW), null);
        domestic.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "", "0",
                Instant.now()));

        assertEquals(KisOrderStatus.UNKNOWN, domestic.getStatus());
        assertNull(domestic.getBrokerOrderNumber());
        assertNull(domestic.getBrokerOrganizationNumber());
    }

    @Test
    void staleBrokerSnapshotCannotReducePreviouslyConfirmedFillQuantity() {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 3,
                        new BigDecimal("70000"), Currency.KRW), null);
        intent.reconcile(2, 1, new BigDecimal("69900"), false, false);

        intent.reconcile(1, 2, new BigDecimal("69800"), false, false);

        assertEquals(2, intent.getFilledQuantity());
        assertEquals(1, intent.getRemainingQuantity());
        assertEquals(new BigDecimal("69900"), intent.getAverageFillPrice());
    }

    @Test
    void realBuyExitThresholdsAreDerivedFromPersistedPlanAndObservedAverageFill() {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL,
                new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 4,
                        new BigDecimal("105"), Currency.USD, "ORB_VWAP", "model-1"), "NASD",
                new BigDecimal("4"), new BigDecimal("2"));

        assertEquals(KisRealExitStatus.WAITING_FOR_ENTRY, intent.getRealExitStatus());
        assertEquals(new BigDecimal("4"), intent.getExitStopDistancePerShare());
        assertEquals(new BigDecimal("2"), intent.getExitRewardRiskMultiple());
        assertNull(intent.getExitStopPrice());
        assertNull(intent.getExitTargetPrice());

        intent.reconcile(2, 2, new BigDecimal("104.500000"), false, false);
        assertEquals(KisRealExitStatus.WAITING_FOR_ENTRY, intent.getRealExitStatus());
        intent.reconcile(4, 0, new BigDecimal("104.500000"), false, false);

        assertEquals(KisRealExitStatus.ARMED, intent.getRealExitStatus());
        assertEquals(new BigDecimal("100.500000"), intent.getExitStopPrice());
        assertEquals(new BigDecimal("112.500000"), intent.getExitTargetPrice());
    }

    @Test
    void invalidExitPlanCannotBeStored() {
        assertThrows(IllegalArgumentException.class, () -> new KisOrderIntent(UUID.randomUUID(), "hash",
                KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("100"), Currency.KRW), null,
                new BigDecimal("100"), new BigDecimal("2")));
        assertThrows(IllegalArgumentException.class, () -> new KisOrderIntent(UUID.randomUUID(), "hash",
                KisEnvironment.PAPER,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("100"), Currency.KRW), null,
                new BigDecimal("5"), new BigDecimal("2")));
        assertThrows(IllegalArgumentException.class, () -> new KisOrderIntent(UUID.randomUUID(), "hash",
                KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.SELL, 1,
                        new BigDecimal("100"), Currency.KRW), null,
                new BigDecimal("5"), new BigDecimal("2")));
    }

    @Test
    void rejectedEntryClosesItsUnusedProtectivePlan() {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("100"), Currency.KRW), null,
                new BigDecimal("5"), new BigDecimal("2"));

        intent.reject("RISK_BLOCKED");

        assertEquals(KisOrderStatus.REJECTED, intent.getStatus());
        assertEquals(KisRealExitStatus.CLOSED, intent.getRealExitStatus());
    }
}
