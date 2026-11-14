package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
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
    @Autowired private KisMarketAiEvidenceClient marketAiEvidence;
    @Autowired private KisRealOrderController realOrderController;

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
        assertNotNull(snapshot.cashBalance(), "Domestic REAL cash balance must be mapped.");
        assertTrue(snapshot.cashBalance().signum() >= 0, "Domestic REAL cash balance cannot be negative.");
        assertNull(snapshot.orderableCash(), "Generic balance reads must not infer orderable cash without an order context.");
        assertNotNull(snapshot.positions(), "Domestic REAL positions must be present even when the account is empty.");
        assertNotNull(snapshot.retrievedAt(), "Domestic REAL account snapshot must have a retrieval timestamp.");
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "KIS_TEST_REAL_ORDERABLE_READS", matches = "true")
    void readsDomesticRealOrderableCashForSymbolWithoutPlacingOrders() {
        String symbol = System.getenv("KIS_REAL_ACCOUNT_ORDERABLE_TEST_SYMBOL");
        String priceText = System.getenv("KIS_REAL_ACCOUNT_ORDERABLE_TEST_UNIT_PRICE");
        assertEquals("005930", symbol, "The domestic read-only test uses the KR watchlist symbol.");
        assertNotNull(priceText, "A current domestic limit price must be supplied to the buying-power query.");
        var unitPrice = new java.math.BigDecimal(priceText);
        assertTrue(unitPrice.signum() > 0, "The domestic limit price must be positive.");

        KisAccountSnapshot snapshot = client.getBalance(KisEnvironment.REAL, Market.KR, symbol, unitPrice, null);
        assertEquals(KisEnvironment.REAL, snapshot.environment());
        assertEquals(Market.KR, snapshot.market());
        assertEquals("KRW", snapshot.currency());
        assertNotNull(snapshot.cashBalance(), "Domestic REAL cash balance must be mapped.");
        assertNotNull(snapshot.orderableCash(), "Domestic REAL orderable cash must be mapped for this symbol and price.");
        assertTrue(snapshot.orderableCash().signum() >= 0, "Domestic REAL orderable cash cannot be negative.");
        assertNotNull(snapshot.positions(), "Domestic REAL positions must be present even when the account is empty.");
    }

    @Test
    void preflightsDomesticRealBuyWithFreshQuoteWithoutSubmittingAnOrder() {
        var quote = marketAiEvidence.quoteForExit(Market.KR, "005930", null);
        var request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                quote.price(), Currency.KRW);

        KisRealOrderPreflightResponse response = realOrderController.preflight(request, null);

        assertNotNull(response);
        assertNotNull(response.assessedAt());
        assertEquals(Market.KR, response.effectiveOrder().market());
        assertEquals("005930", response.effectiveOrder().symbol());
        assertTrue(response.effectiveOrder().quantity() > 0);
        System.out.printf("KIS REAL domestic order-free preflight approved=%s reasonCount=%d (financial values suppressed)%n",
                response.approved(), response.reasons().size());
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
