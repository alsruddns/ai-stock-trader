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
    }

    private static PaperFillRepository.FillTradeView trade(String side, int quantity, String price, Instant at) {
        PaperFillRepository.FillTradeView trade = mock(PaperFillRepository.FillTradeView.class);
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
