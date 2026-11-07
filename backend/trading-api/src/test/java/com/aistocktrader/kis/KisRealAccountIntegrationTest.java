package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.time.LocalDate;
import java.util.ArrayList;

import static org.junit.jupiter.api.Assertions.*;

/** Explicit opt-in only: performs read-only REAL account and statement GET requests; never submits orders. */
@EnabledIfEnvironmentVariable(named = "RUN_KIS_REAL_ACCOUNT_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisRealAccountIntegrationTest {
    @Autowired private KisAccountClient client;
    @Autowired private KisRealOrderClient realOrders;
    @Autowired private KisMarketSessionClient sessions;

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
        assertEquals("false", System.getenv().getOrDefault("KIS_PAPER_ORDERS_ENABLED", "false").toLowerCase());
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

    @Test
    void readsDomesticRealizedPnlFieldNamesWithoutPrintingFinancialValues() {
        KisRealizedPnlStatement statement = client.getRealizedPnlStatement(
                Market.KR, LocalDate.of(2026, 11, 5), LocalDate.of(2026, 11, 6), null);
        assertEquals(Market.KR, statement.market());
        assertEquals("KIS_READ_ONLY_REALIZED_PNL_REST", statement.source());
        printFieldNamesOnly(statement);
    }

    @Test
    void readsUsRealizedPnlFieldNamesWithoutPrintingFinancialValues() {
        KisRealizedPnlStatement statement = client.getRealizedPnlStatement(
                Market.US, LocalDate.of(2026, 11, 5), LocalDate.of(2026, 11, 6), "NASD");
        assertEquals(Market.US, statement.market());
        assertEquals("KIS_READ_ONLY_REALIZED_PNL_REST", statement.source());
        printFieldNamesOnly(statement);
    }

    @Test
    void readsDomesticSessionExecutionsWithoutPrintingTradeDetails() {
        var session = sessions.latestClosedSession(Market.KR);
        assertTrue(session.session());

        var executions = realOrders.getRealSessionExecutions(Market.KR, session.sessionDate());

        assertNotNull(executions);
        executions.forEach(execution -> {
            assertFalse(execution.orderNumber().isBlank());
            assertFalse(execution.symbol().isBlank());
            assertNotNull(execution.side());
        });
        System.out.printf("KIS REAL execution history market=KR session=%s rows=%d (details suppressed)%n",
                session.sessionDate(), executions.size());
    }

    @Test
    void readsUsSessionExecutionsWithoutPrintingTradeDetails() {
        var session = sessions.latestClosedSession(Market.US);
        assertTrue(session.session());

        var executions = realOrders.getRealSessionExecutions(Market.US, session.sessionDate());

        assertNotNull(executions);
        executions.forEach(execution -> {
            assertFalse(execution.orderNumber().isBlank());
            assertFalse(execution.symbol().isBlank());
            assertNotNull(execution.side());
        });
        System.out.printf("KIS REAL execution history market=US session=%s rows=%d (details suppressed)%n",
                session.sessionDate(), executions.size());
    }

    private static void printFieldNamesOnly(KisRealizedPnlStatement statement) {
        System.out.printf("KIS REAL statement schema market=%s detailFields=%s summaryFields=%s; values suppressed%n",
                statement.market(), fieldNames(statement.details()), fieldNames(statement.summary()));
    }

    private static String fieldNames(JsonNode node) {
        JsonNode sample = node.isArray() && !node.isEmpty() ? node.get(0) : node;
        if (!sample.isObject()) return "[]";
        ArrayList<String> names = new ArrayList<>();
        sample.fieldNames().forEachRemaining(names::add);
        return String.join(",", names);
    }
}
