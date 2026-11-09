package com.aistocktrader.order;

import com.aistocktrader.kis.KisApiException;
import com.aistocktrader.kis.MarketOperationalScope;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class OrderControllerMarketScopeTest {
    @Test
    void blocksOverseasLocalPaperOrderBeforeWritingTheLedger() {
        OrderController controller = new OrderController(null, null, MarketOperationalScope.domesticOnly());
        OrderRequest request = new OrderRequest(Market.US, "AAPL", OrderSide.BUY,
                1, BigDecimal.ONE, Currency.USD);

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.paperOrder(UUID.randomUUID(), request));

        assertEquals(org.springframework.http.HttpStatus.LOCKED, exception.status());
    }
}
