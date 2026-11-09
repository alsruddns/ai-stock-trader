package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class MarketOperationalScopeTest {
    @Test
    void defaultRuntimeScopeAllowsDomesticAndRejectsOverseasOperations() {
        MarketOperationalScope scope = new MarketOperationalScope("KR");

        scope.require(Market.KR, "domestic validation");
        KisApiException exception = assertThrows(KisApiException.class,
                () -> scope.require(Market.US, "overseas validation"));

        assertEquals(HttpStatus.LOCKED, exception.status());
    }

    @Test
    void invalidOrEmptyScopeFailsClosed() {
        assertThrows(IllegalStateException.class, () -> new MarketOperationalScope(""));
        assertThrows(IllegalStateException.class, () -> new MarketOperationalScope("KR,XX"));
    }
}
