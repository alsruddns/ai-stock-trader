package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRealOrderControllerTest {
    private final KisRealOrderOrchestrator orders = mock(KisRealOrderOrchestrator.class);
    private final KisOrderIntentStore intents = mock(KisOrderIntentStore.class);
    private final KisAccountClient accounts = mock(KisAccountClient.class);

    @Test
    void paperModeRejectsRealSubmitBeforeReadingAccountOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), null, buyRequest()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders);
    }

    @Test
    void paperModeRejectsRealReconciliationBeforeLoadingIntentOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.reconcile(UUID.randomUUID()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(intents, accounts, orders);
    }

    @Test
    void paperModeRejectsRealCancellationBeforeLoadingIntentOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.cancel(UUID.randomUUID()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(intents, accounts, orders);
    }

    @Test
    void enabledRealPathReadsCurrentAccountBeforeDelegatingTheOrder() {
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true,
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100));
        KisProperties kis = properties(true, true);
        KisRealOrderController controller = controller(safety, kis);
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000),
                List.of(), Instant.now(), "test");
        KisOrderIntentResponse expected = new KisOrderIntentResponse(key, "real", "KR", "005930", "BUY", 1,
                "1000", "KRW", KisOrderStatus.ACCEPTED, "broker-order", "broker-org", "0",
                0, 1, null, Instant.now(), Instant.now());
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(1000), null))
                .thenReturn(account);
        when(orders.submit(key, request, null, account)).thenReturn(expected);

        var response = controller.submit(key, null, request);

        assertEquals(HttpStatus.ACCEPTED, response.getStatusCode());
        assertEquals(expected, response.getBody());
        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(1000), null);
        verify(orders).submit(key, request, null, account);
    }

    private KisRealOrderController controller(TradingSafetyProperties safety, KisProperties kis) {
        return new KisRealOrderController(orders, intents, accounts, kis, safety);
    }

    private static KisProperties properties(boolean allReads, boolean realRead) {
        return new KisProperties(allReads, false, realRead, false,
                new KisProperties.Account("", "", ""), new KisProperties.Account("", "", ""),
                null, null, null);
    }

    private static OrderRequest buyRequest() {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                BigDecimal.valueOf(1000), Currency.KRW);
    }
}
