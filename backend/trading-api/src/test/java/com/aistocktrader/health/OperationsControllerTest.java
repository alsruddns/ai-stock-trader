package com.aistocktrader.health;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.OrderRepository;
import com.aistocktrader.order.PaperCashRepository;
import com.aistocktrader.order.PaperFillRepository;
import com.aistocktrader.order.PaperPositionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Sort;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class OperationsControllerTest {
    @Test
    void dailyRealizedPnlUsesAverageCostAndKeepsCurrenciesSeparate() {
        OrderRepository orders = mock(OrderRepository.class);
        PaperPositionRepository positions = mock(PaperPositionRepository.class);
        PaperCashRepository cash = mock(PaperCashRepository.class);
        PaperFillRepository fills = mock(PaperFillRepository.class);
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
                false, false);

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
                false, false).summary();

        assertEquals(1, summary.consecutiveLosingTrades());
    }

    private static PaperFillRepository.FillTradeView trade(String side, int quantity, String price, Instant at) {
        return trade(side, quantity, price, at, side.equals("SELL") ? 1L : 0L);
    }

    private static PaperFillRepository.FillTradeView trade(String side, int quantity, String price, Instant at, long orderId) {
        PaperFillRepository.FillTradeView trade = mock(PaperFillRepository.FillTradeView.class);
        when(trade.getOrderId()).thenReturn(orderId);
        when(trade.getFilledAt()).thenReturn(at);
        when(trade.getQuantity()).thenReturn(quantity);
        when(trade.getFillPrice()).thenReturn(new BigDecimal(price));
        when(trade.getMarket()).thenReturn("US");
        when(trade.getSymbol()).thenReturn("AAPL");
        when(trade.getCurrency()).thenReturn("USD");
        when(trade.getSide()).thenReturn(side);
        return trade;
    }
}
