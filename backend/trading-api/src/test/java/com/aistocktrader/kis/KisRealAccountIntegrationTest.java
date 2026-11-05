package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.junit.jupiter.api.Assertions.*;

/** Explicit opt-in only: performs read-only REAL account balance GET requests; never submits orders. */
@EnabledIfEnvironmentVariable(named = "RUN_KIS_REAL_ACCOUNT_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisRealAccountIntegrationTest {
    @Autowired private KisAccountClient client;

    @BeforeAll
    static void assertReadOnlyGates() {
        KisProperties.Account real = new KisProperties.Account(
                System.getenv("KIS_REAL_APP_KEY"), System.getenv("KIS_REAL_APP_SECRET"),
                System.getenv("KIS_REAL_ACCOUNT_NUMBER"));
        assertTrue(real.configured(), "REAL KIS credentials must be configured locally.");
        assertEquals("true", System.getenv("KIS_ACCOUNT_READ_ENABLED"));
        assertEquals("true", System.getenv("KIS_REAL_ACCOUNT_READ_ENABLED"));
        assertEquals("paper", System.getenv().getOrDefault("TRADING_MODE", "paper").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("LIVE_TRADING_ENABLED", "false").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("KIS_REAL_ORDERS_ENABLED", "false").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("KIS_REAL_RECONCILIATION_ENABLED", "false").toLowerCase());
    }

    @Test
    void readsDomesticRealBalanceWithoutPlacingOrders() {
        KisAccountSnapshot snapshot = client.getBalance(KisEnvironment.REAL, Market.KR, null, null, null);
        assertEquals(KisEnvironment.REAL, snapshot.environment());
        assertEquals(Market.KR, snapshot.market());
        assertEquals("KRW", snapshot.currency());
    }

    @Test
    void readsUsRealBalanceWithoutPlacingOrders() {
        KisAccountSnapshot snapshot = client.getBalance(KisEnvironment.REAL, Market.US, null, null, "NASD");
        assertEquals(KisEnvironment.REAL, snapshot.environment());
        assertEquals(Market.US, snapshot.market());
        assertEquals("USD", snapshot.currency());
    }
}
