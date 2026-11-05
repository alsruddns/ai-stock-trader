package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.junit.jupiter.api.Assertions.*;

/** Explicit opt-in only: performs read-only paper account GET requests. */
@EnabledIfEnvironmentVariable(named = "RUN_KIS_PAPER_ACCOUNT_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisPaperAccountIntegrationTest {
    @Autowired private KisAccountClient client;

    @BeforeAll
    static void configure() {
        KisProperties.Account paper = new KisProperties.Account(
                System.getenv("KIS_PAPER_APP_KEY"), System.getenv("KIS_PAPER_APP_SECRET"),
                System.getenv("KIS_PAPER_ACCOUNT_NUMBER"));
        assertTrue(paper.configured(), "Paper KIS credentials must be configured for this opt-in test.");
        assertEquals("true", System.getenv("KIS_ACCOUNT_READ_ENABLED"),
                "Set KIS_ACCOUNT_READ_ENABLED=true only when intentionally running this read-only test.");
        assertEquals("paper", System.getenv().getOrDefault("TRADING_MODE", "paper").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("LIVE_TRADING_ENABLED", "false").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("KIS_PAPER_ORDERS_ENABLED", "false").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("KIS_REAL_ORDERS_ENABLED", "false").toLowerCase());
        assertEquals("false", System.getenv().getOrDefault("KIS_REAL_RECONCILIATION_ENABLED", "false").toLowerCase());
    }

    @Test
    void readsDomesticPaperBalanceWithoutPlacingOrders() {
        KisAccountSnapshot snapshot = client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null);
        assertEquals(KisEnvironment.PAPER, snapshot.environment());
        assertEquals(Market.KR, snapshot.market());
        assertEquals("KRW", snapshot.currency());
    }

    @Test
    void readsUsPaperBalanceWithoutPlacingOrders() {
        KisAccountSnapshot snapshot = client.getBalance(KisEnvironment.PAPER, Market.US, null, null, "NASD");
        assertEquals(KisEnvironment.PAPER, snapshot.environment());
        assertEquals(Market.US, snapshot.market());
        assertEquals("USD", snapshot.currency());
    }
}
