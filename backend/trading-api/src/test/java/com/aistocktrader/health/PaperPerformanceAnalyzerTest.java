package com.aistocktrader.health;

import com.aistocktrader.order.PaperFillRepository;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class PaperPerformanceAnalyzerTest {
    private final PaperPerformanceAnalyzer analyzer = new PaperPerformanceAnalyzer();

    @Test
    void groupsSellFillOutcomesByInstrumentAndIncludesEntryAndExitCosts() {
        Instant now = Instant.now();
        var result = analyzer.analyze(List.of(
                trade(1L, "BUY", 3, "100", "3", "KR", "005930", "KRW", now),
                trade(2L, "SELL", 1, "110", "1", "KR", "005930", "KRW", now.plusSeconds(1)),
                trade(2L, "SELL", 1, "110", "1", "KR", "005930", "KRW", now.plusSeconds(2)),
                trade(3L, "SELL", 1, "90", "0", "KR", "005930", "KRW", now.plusSeconds(3))));

        assertEquals(1, result.size());
        var performance = result.getFirst();
        assertEquals("KR", performance.market());
        assertEquals("005930", performance.symbol());
        assertEquals(2L, performance.realizedSellOrders());
        assertEquals(1L, performance.wins());
        assertEquals(1L, performance.losses());
        assertEquals(new BigDecimal("5.000000"), performance.netPnl());
        assertEquals(new BigDecimal("16.000000"), performance.averageWin());
        assertEquals(new BigDecimal("-11.000000"), performance.averageLoss());
        assertEquals(new BigDecimal("0.500000"), performance.winRate());
    }

    @Test
    void rejectsPaperHistoryThatSellsMoreThanTheRecordedPosition() {
        Instant now = Instant.now();
        assertThrows(IllegalStateException.class, () -> analyzer.analyze(List.of(
                trade(1L, "SELL", 1, "100", "0", "US", "AAPL", "USD", now))));
    }

    private static PaperFillRepository.FillTradeView trade(long orderId, String side, int quantity,
                                                             String price, String cost, String market,
                                                             String symbol, String currency, Instant at) {
        var row = mock(PaperFillRepository.FillTradeView.class);
        when(row.getOrderId()).thenReturn(orderId);
        when(row.getSide()).thenReturn(side);
        when(row.getQuantity()).thenReturn(quantity);
        when(row.getFillPrice()).thenReturn(new BigDecimal(price));
        when(row.getTransactionCost()).thenReturn(new BigDecimal(cost));
        when(row.getMarket()).thenReturn(market);
        when(row.getSymbol()).thenReturn(symbol);
        when(row.getCurrency()).thenReturn(currency);
        when(row.getFilledAt()).thenReturn(at);
        return row;
    }
}
