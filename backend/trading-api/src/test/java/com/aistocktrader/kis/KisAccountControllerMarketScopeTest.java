package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;

class KisAccountControllerMarketScopeTest {
    @Test
    void blocksOverseasAccountReadBeforeCallingTheKisClient() {
        KisAccountClient client = mock(KisAccountClient.class);
        KisAccountController controller = new KisAccountController(
                client, null, null, MarketOperationalScope.domesticOnly());

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.balance("paper", Market.US, "AAPL", BigDecimal.ONE, "NASD"));

        assertEquals(org.springframework.http.HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(client);
    }
}
