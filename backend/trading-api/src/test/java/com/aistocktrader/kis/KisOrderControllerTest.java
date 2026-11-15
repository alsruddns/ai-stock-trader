package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class KisOrderControllerTest {
    private final KisOrderOrchestrator orders = mock(KisOrderOrchestrator.class);
    private final KisOrderIntentStore intents = mock(KisOrderIntentStore.class);
    private final KisOrderController controller = new KisOrderController(
            orders, intents, MarketOperationalScope.domesticOnly());

    @Test
    void blocksOverseasPaperSubmissionBeforeCallingOrderOrIntentServices() {
        OrderRequest request = new OrderRequest(Market.US, "AAPL", OrderSide.BUY,
                1, BigDecimal.ONE, Currency.USD);

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), "NASD", request));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(orders, intents);
    }

    @Test
    void forwardsDomesticPaperSubmissionAndReturnsAcceptedResponse() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY,
                1, new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.PAPER, request, null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));
        KisOrderIntentResponse expected = KisOrderIntentResponse.from(intent);
        when(orders.submitPaperOrder(key, request, null)).thenReturn(expected);

        ResponseEntity<KisOrderIntentResponse> response = controller.submit(key, null, request);

        assertEquals(HttpStatus.ACCEPTED, response.getStatusCode());
        assertEquals(expected, response.getBody());
        verify(orders).submitPaperOrder(key, request, null);
        verifyNoInteractions(intents);
    }

    @Test
    void mapsDefinitivePaperOrderRejectionToUnprocessableEntity() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY,
                1, new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent intent = new KisOrderIntent(key, "hash", KisEnvironment.PAPER, request, null);
        intent.reject("PREFLIGHT_BLOCKED");
        when(orders.submitPaperOrder(key, request, null)).thenReturn(KisOrderIntentResponse.from(intent));

        ResponseEntity<KisOrderIntentResponse> response = controller.submit(key, null, request);

        assertEquals(HttpStatus.UNPROCESSABLE_ENTITY, response.getStatusCode());
        assertEquals(KisOrderStatus.REJECTED, response.getBody().status());
    }

    @Test
    void hidesRealIntentFromPaperOrderRoutes() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY,
                1, new BigDecimal("70000"), Currency.KRW);
        when(intents.get(key)).thenReturn(new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null));

        KisApiException exception = assertThrows(KisApiException.class, () -> controller.get(key));

        assertEquals(HttpStatus.NOT_FOUND, exception.status());
        verify(intents).get(key);
        verifyNoInteractions(orders);
    }

    @Test
    void hidesRealIntentFromPaperReconciliationRoute() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY,
                1, new BigDecimal("70000"), Currency.KRW);
        when(intents.get(key)).thenReturn(new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null));

        KisApiException exception = assertThrows(KisApiException.class, () -> controller.reconcile(key));

        assertEquals(HttpStatus.NOT_FOUND, exception.status());
        verify(intents).get(key);
        verifyNoInteractions(orders);
    }

    @Test
    void hidesRealIntentFromPaperCancellationRoute() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY,
                1, new BigDecimal("70000"), Currency.KRW);
        when(intents.get(key)).thenReturn(new KisOrderIntent(key, "hash", KisEnvironment.REAL, request, null));

        KisApiException exception = assertThrows(KisApiException.class, () -> controller.cancel(key, null));

        assertEquals(HttpStatus.NOT_FOUND, exception.status());
        verify(intents).get(key);
        verifyNoInteractions(orders);
    }

    @Test
    void manualReconciliationRequiresExplicitBrokerIdentityConfirmation() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY,
                1, new BigDecimal("70000"), Currency.KRW);
        when(intents.get(key)).thenReturn(new KisOrderIntent(key, "hash", request, null));

        assertThrows(IllegalArgumentException.class, () -> controller.manualReconcile(key,
                new KisOrderController.ManualPaperOrderReconciliationRequest("1234", "ORG01", false)));

        verifyNoInteractions(orders);
    }
}
