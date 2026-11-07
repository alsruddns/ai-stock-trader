package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KisRealTradingCostPropertiesTest {
    @Test
    void reportsOnlyWhetherEveryMarketRateIsValid() {
        var configured = new KisRealTradingCostProperties("5", "5", "0", "1", "1", "0");
        assertTrue(configured.configuredFor(Market.KR));
        assertTrue(configured.configuredFor(Market.US));

        var incomplete = new KisRealTradingCostProperties("5", "", "0", "1", "1", "0");
        assertFalse(incomplete.configuredFor(Market.KR));
        assertTrue(incomplete.configuredFor(Market.US));

        var outOfRange = new KisRealTradingCostProperties("1001", "5", "0", "1", "1", "0");
        assertFalse(outOfRange.configuredFor(Market.KR));
    }
}
