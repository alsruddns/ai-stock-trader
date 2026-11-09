package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verifyNoInteractions;

class KisOrderControllerMarketScopeTest {
    @Test
    void blocksOverseasPaperSubmissionBeforeCallingTheOrderOrchestrator() {
        KisOrderOrchestrator orders = mock(KisOrderOrchestrator.class);
        KisOrderIntentStore intents = mock(KisOrderIntentStore.class);
        KisOrderController controller = new KisOrderController(
                orders, intents, MarketOperationalScope.domesticOnly());
        OrderRequest request = new OrderRequest(Market.US, "AAPL", OrderSide.BUY,
                1, BigDecimal.ONE, Currency.USD);

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), "NASD", request));

        assertEquals(org.springframework.http.HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(orders, intents);
    }

    @Test
    void blocksOutOfScopePaperIntentReadReconciliationAndCancellationBeforeBrokerAccess() {
        KisOrderOrchestrator orders = mock(KisOrderOrchestrator.class);
        KisOrderIntentStore intents = mock(KisOrderIntentStore.class);
        KisOrderController controller = new KisOrderController(
                orders, intents, MarketOperationalScope.domesticOnly());
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.US, "AAPL", OrderSide.BUY,
                1, BigDecimal.ONE, Currency.USD);
        when(intents.get(key)).thenReturn(new KisOrderIntent(key, "hash", request, "NASD"));

        assertEquals(org.springframework.http.HttpStatus.LOCKED,
                assertThrows(KisApiException.class, () -> controller.get(key)).status());
        assertEquals(org.springframework.http.HttpStatus.LOCKED,
                assertThrows(KisApiException.class, () -> controller.reconcile(key)).status());
        assertEquals(org.springframework.http.HttpStatus.LOCKED,
                assertThrows(KisApiException.class, () -> controller.cancel(key, "NASD")).status());

        verifyNoInteractions(orders);
    }
}
