package com.aistocktrader.health;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.kis.KisEnvironment;
import com.aistocktrader.kis.KisOrderIntent;
import com.aistocktrader.kis.KisOrderIntentRepository;
import com.aistocktrader.kis.KisOrderStatus;
import com.aistocktrader.kis.KisOrderReceipt;
import com.aistocktrader.kis.KisProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderRepository;
import com.aistocktrader.order.PaperCashRepository;
import com.aistocktrader.order.PaperFillRepository;
import com.aistocktrader.order.PaperPositionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Sort;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OperationsControllerTest {
    @Test
    void operationsSummaryReportsOnlyWhetherEnvironmentCredentialsAreConfigured() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
        when(orders.findAll()).thenReturn(List.of());
        when(orders.findAll(any(Sort.class))).thenReturn(List.of());
        when(positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)).thenReturn(List.of());
        when(cash.findAll()).thenReturn(List.of());
        when(fills.countByFilledAtAfter(any())).thenReturn(0L);
        when(fills.findAllWithTradeDetails()).thenReturn(List.of());
        KisProperties credentials = new KisProperties(false, false, false, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("", "", ""), null, null, null);

        var summary = new OperationsController(orders, positions, cash, fills,
                new TradingSafetyProperties("paper", false, BigDecimal.TEN, BigDecimal.TEN),
                emptyKisIntents(), credentials, false, false, false, false, false, false, false).summary();

        assertEquals(true, summary.kisPaperCredentialsConfigured());
        assertEquals(false, summary.kisRealCredentialsConfigured());
        assertEquals(false, summary.kisPaperAccountReadsEnabled());
        assertEquals(false, summary.kisPaperDomesticCancelSupported());
        assertEquals(false, summary.realProtectiveExitManagerEnabled());
        assertEquals(false, summary.kisRealProtectiveExitAutomationEnabled());
    }

    @Test
    void dailyRealizedPnlUsesAverageCostAndKeepsCurrenciesSeparate() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
        KisOrderIntentRepository kisIntents = emptyKisIntents();
        when(orders.findAll()).thenReturn(List.of());
        when(orders.findAll(any(Sort.class))).thenReturn(List.of());
        when(positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)).thenReturn(List.of());
        when(cash.findAll()).thenReturn(List.of());
        when(fills.countByFilledAtAfter(any())).thenReturn(2L);
        var buy = trade("BUY", 10, "100", Instant.now().minusSeconds(20));
        var sell = trade("SELL", 4, "110", Instant.now().minusSeconds(5));
        when(fills.findAllWithTradeDetails()).thenReturn(List.of(buy, sell));

        OperationsController controller = new OperationsController(orders, positions, cash, fills,
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100")),
                kisIntents, false, false, false, false, false);

        var summary = controller.summary();

        assertEquals(new BigDecimal("40.000000"), summary.realizedPnlTodayGrossByCurrency().get("USD"));
        assertEquals(BigDecimal.ZERO, summary.realizedPnlTodayGrossByCurrency().get("KRW"));
        assertEquals(0, summary.consecutiveLosingTrades());
    }

    @Test
    void consecutiveLossCounterStopsAtMostRecentProfitableSellOrder() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
        KisOrderIntentRepository kisIntents = emptyKisIntents();
        when(orders.findAll()).thenReturn(List.of());
        when(orders.findAll(any(Sort.class))).thenReturn(List.of());
        when(positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)).thenReturn(List.of());
        when(cash.findAll()).thenReturn(List.of());
        when(fills.countByFilledAtAfter(any())).thenReturn(0L);
        Instant now = Instant.now();
        var history = List.of(
                trade("BUY", 3, "100", now.minusSeconds(60), 10L),
                trade("SELL", 1, "90", now.minusSeconds(50), 11L),
                trade("BUY", 1, "100", now.minusSeconds(40), 12L),
                trade("SELL", 1, "90", now.minusSeconds(30), 13L),
                trade("SELL", 1, "120", now.minusSeconds(20), 14L),
                trade("BUY", 1, "120", now.minusSeconds(10), 15L),
                trade("SELL", 1, "100", now.minusSeconds(1), 16L));
        when(fills.findAllWithTradeDetails()).thenReturn(history);

        var summary = new OperationsController(orders, positions, cash, fills,
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100")),
                kisIntents, false, false, false, false, false).summary();

        assertEquals(1, summary.consecutiveLosingTrades());
    }

    @Test
    void operationsSummarySeparatelyExposesRealIntentStatusWithoutBrokerIdentifiers() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
        KisOrderIntentRepository kisIntents = emptyKisIntents();
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", "BUY", "9001", "ORG01", "0", Instant.now()));
        when(kisIntents.countStatusesByEnvironment(KisEnvironment.REAL))
                .thenReturn(List.<Object[]>of(new Object[]{KisOrderStatus.ACCEPTED, 1L}));
        when(kisIntents.findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment.REAL)).thenReturn(List.of(intent));
        when(orders.findAll()).thenReturn(List.of());
        when(orders.findAll(any(Sort.class))).thenReturn(List.of());
        when(positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)).thenReturn(List.of());
        when(cash.findAll()).thenReturn(List.of());
        when(fills.countByFilledAtAfter(any())).thenReturn(0L);
        when(fills.findAllWithTradeDetails()).thenReturn(List.of());

        var summary = new OperationsController(orders, positions, cash, fills,
                new TradingSafetyProperties("paper", false, BigDecimal.TEN, BigDecimal.TEN), kisIntents,
                false, false, false, false, false).summary();

        assertEquals("paper", summary.tradingMode());
        assertEquals(1L, summary.realOrderCounts().get(KisOrderStatus.ACCEPTED));
        assertEquals("005930", summary.recentRealOrders().getFirst().symbol());
        assertEquals(KisOrderStatus.ACCEPTED, summary.recentRealOrders().getFirst().status());
    }

    @Test
    void operationsSummaryExposesPaperKisOrderRejectionForReadOnlyMonitoring() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
        KisOrderIntentRepository kisIntents = emptyKisIntents();
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.PAPER,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW), null);
        intent.reject("EGW00201");
        when(kisIntents.countStatusesByEnvironment(KisEnvironment.PAPER))
                .thenReturn(List.<Object[]>of(new Object[]{KisOrderStatus.REJECTED, 1L}));
        when(kisIntents.findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment.PAPER)).thenReturn(List.of(intent));
        when(orders.findAll()).thenReturn(List.of());
        when(orders.findAll(any(Sort.class))).thenReturn(List.of());
        when(positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)).thenReturn(List.of());
        when(cash.findAll()).thenReturn(List.of());
        when(fills.countByFilledAtAfter(any())).thenReturn(0L);
        when(fills.findAllWithTradeDetails()).thenReturn(List.of());

        var summary = new OperationsController(orders, positions, cash, fills,
                new TradingSafetyProperties("paper", false, BigDecimal.TEN, BigDecimal.TEN), kisIntents,
                false, false, false, false, false).summary();

        assertEquals(1L, summary.paperKisOrderCounts().get(KisOrderStatus.REJECTED));
        assertEquals("005930", summary.recentPaperKisOrders().getFirst().symbol());
        assertEquals(KisOrderStatus.REJECTED, summary.recentPaperKisOrders().getFirst().status());
        assertEquals("EGW00201", summary.recentPaperKisOrders().getFirst().responseCode());
    }

    @Test
    void dailyNetPnlIncludesBuyAndSellCostsWhileGrossPnlRemainsAvailable() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
        KisOrderIntentRepository kisIntents = emptyKisIntents();
        Instant now = Instant.now();
        when(orders.findAll()).thenReturn(List.of());
        when(orders.findAll(any(Sort.class))).thenReturn(List.of());
        when(positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)).thenReturn(List.of());
        when(cash.findAll()).thenReturn(List.of());
        when(fills.countByFilledAtAfter(any())).thenReturn(1L);
        var buyTrade = trade("BUY", 10, "100", now.minusSeconds(10), 1L, new BigDecimal("10"));
        var sellTrade = trade("SELL", 4, "110", now, 2L, new BigDecimal("3"));
        when(fills.findAllWithTradeDetails()).thenReturn(List.of(buyTrade, sellTrade));

        var summary = new OperationsController(orders, positions, cash, fills,
                new TradingSafetyProperties("paper", false, BigDecimal.TEN, BigDecimal.TEN), kisIntents,
                false, false, false, false, true).summary();

        assertEquals(new BigDecimal("40.000000"), summary.realizedPnlTodayGrossByCurrency().get("USD"));
        assertEquals(new BigDecimal("33.000000"), summary.realizedPnlTodayNetByCurrency().get("USD"));
        assertEquals(new BigDecimal("13"), summary.paperTransactionCostsTodayByCurrency().get("USD"));
        assertEquals(true, summary.paperCostModelEnabled());
    }

    private static KisOrderIntentRepository emptyKisIntents() {
        KisOrderIntentRepository repository = mock(KisOrderIntentRepository.class);
        when(repository.countStatusesByEnvironment(KisEnvironment.REAL)).thenReturn(List.of());
        when(repository.countStatusesByEnvironment(KisEnvironment.PAPER)).thenReturn(List.of());
        when(repository.findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment.REAL)).thenReturn(List.of());
        when(repository.findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment.PAPER)).thenReturn(List.of());
        return repository;
    }

    private static PaperFillRepository.FillTradeView trade(String side, int quantity, String price, Instant at) {
        return trade(side, quantity, price, at, side.equals("SELL") ? 1L : 0L);
    }

    private static PaperFillRepository.FillTradeView trade(String side, int quantity, String price, Instant at, long orderId) {
        return trade(side, quantity, price, at, orderId, BigDecimal.ZERO);
    }

    private static PaperFillRepository.FillTradeView trade(String side, int quantity, String price, Instant at,
                                                            long orderId, BigDecimal transactionCost) {
        PaperFillRepository.FillTradeView trade = mock(PaperFillRepository.FillTradeView.class);
        when(trade.getOrderId()).thenReturn(orderId);
        when(trade.getFilledAt()).thenReturn(at);
        when(trade.getQuantity()).thenReturn(quantity);
        when(trade.getFillPrice()).thenReturn(new BigDecimal(price));
        when(trade.getTransactionCost()).thenReturn(transactionCost);
        when(trade.getMarket()).thenReturn("US");
        when(trade.getSymbol()).thenReturn("AAPL");
        when(trade.getCurrency()).thenReturn("USD");
        when(trade.getSide()).thenReturn(side);
        return trade;
    }
}
