package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class KisPaperExitPlanLifecycleServiceTest {
    @Mock KisPaperExitPlanRepository plans;
    @Mock KisOrderIntentRepository intents;

    @Test
    void fullyFilledPaperEntryArmsItsExitPlan() {
        UUID key = UUID.randomUUID();
        KisPaperExitPlan plan = new KisPaperExitPlan(key);
        KisOrderIntent entry = entry(key, OrderSide.BUY, "005930", 2, true);
        entry.reconcile(2, 0, new BigDecimal("70000"), false, false);
        when(plans.findById(key)).thenReturn(Optional.of(plan));
        when(intents.findById(key)).thenReturn(Optional.of(entry));

        assertEquals(KisPaperExitStatus.ARMED, service().synchronize(key));
        verify(plans).arm(key);
    }

    @Test
    void verifiedNoOrderClosesAnEntryPlanEvenThoughOriginalQuantityRemains() {
        UUID key = UUID.randomUUID();
        KisPaperExitPlan plan = new KisPaperExitPlan(key);
        KisOrderIntent entry = entry(key, OrderSide.BUY, "005930", 1, true);
        entry.markUnknown();
        entry.confirmNoPaperOrderWasSubmitted();
        when(plans.findById(key)).thenReturn(Optional.of(plan));
        when(intents.findById(key)).thenReturn(Optional.of(entry));

        assertEquals(KisPaperExitStatus.CLOSED, service().synchronize(key));
        verify(plans).closePlan(key);
    }

    @Test
    void partialEntryRequiresManualReviewUntilRemainderAndPositionCanBeManagedTogether() {
        UUID key = UUID.randomUUID();
        KisPaperExitPlan plan = new KisPaperExitPlan(key);
        KisOrderIntent entry = entry(key, OrderSide.BUY, "005930", 2, true);
        entry.reconcile(1, 1, new BigDecimal("70000"), false, false);
        when(plans.findById(key)).thenReturn(Optional.of(plan));
        when(intents.findById(key)).thenReturn(Optional.of(entry));

        assertEquals(KisPaperExitStatus.MANUAL_REVIEW, service().synchronize(key));
        verify(plans).requireManualReview(key);
    }

    @Test
    void filledExitWithWrongPriceRequiresManualReview() {
        UUID entryKey = UUID.randomUUID();
        UUID exitKey = UUID.randomUUID();
        KisPaperExitPlan plan = mock(KisPaperExitPlan.class);
        when(plan.getEntryIdempotencyKey()).thenReturn(entryKey);
        when(plan.getStatus()).thenReturn(KisPaperExitStatus.EXIT_PENDING);
        when(plan.getExitOrderKey()).thenReturn(exitKey);
        when(plan.getExitQuantity()).thenReturn(2);
        when(plan.getExitLimitPrice()).thenReturn(new BigDecimal("71000"));
        KisOrderIntent entry = entry(entryKey, OrderSide.BUY, "005930", 2, true);
        entry.reconcile(2, 0, new BigDecimal("70000"), false, false);
        KisOrderIntent exit = entry(exitKey, OrderSide.SELL, "005930", 2, false);
        exit.reconcile(2, 0, new BigDecimal("70900"), false, false);
        when(plans.findById(entryKey)).thenReturn(Optional.of(plan));
        when(intents.findById(entryKey)).thenReturn(Optional.of(entry));
        when(intents.findById(exitKey)).thenReturn(Optional.of(exit));

        assertEquals(KisPaperExitStatus.MANUAL_REVIEW, service().synchronize(entryKey));
        verify(plans).requireManualReview(entryKey);
    }

    private KisPaperExitPlanLifecycleService service() {
        return new KisPaperExitPlanLifecycleService(plans, intents);
    }

    private static KisOrderIntent entry(UUID key, OrderSide side, String symbol, int quantity,
                                        boolean withExitPlan) {
        OrderRequest request = new OrderRequest(Market.KR, symbol, side, quantity,
                new BigDecimal("70000"), Currency.KRW, "ORB_VWAP", "model-a");
        return new KisOrderIntent(key, "hash-" + key, KisEnvironment.PAPER, request, "J",
                withExitPlan ? new BigDecimal("1000") : null,
                withExitPlan ? new BigDecimal("2") : null);
    }
}
