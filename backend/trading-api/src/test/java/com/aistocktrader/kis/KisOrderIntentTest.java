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
}
