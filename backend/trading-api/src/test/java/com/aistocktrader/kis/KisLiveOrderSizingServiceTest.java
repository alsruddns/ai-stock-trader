package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;

class KisLiveOrderSizingServiceTest {
    @Test
    void capsProposedQuantityAtThePerTradeRiskBudget() {
        KisLiveOrderSizingService sizing = sizing("100000000", "100.00");
        OrderRequest request = buy(1000, "10000");

        OrderRequest result = sizing.capBuyQuantity(request, account("1000000", "1000000", List.of()),
                new BigDecimal("100"));

        assertEquals(25, result.quantity());
        assertEquals("10000", result.limitPrice().toPlainString());
        assertEquals("model.v1", result.modelId());
    }

    @Test
    void capsAtOrderableCashAndPositionExposureAsWellAsTheRequestedUpperBound() {
        KisLiveOrderSizingService sizing = sizing("100000000", "10.00");
        OrderRequest request = buy(50, "1000");
        OrderRequest cashLimited = sizing.capBuyQuantity(request, account("1000000", "10000", List.of()),
                new BigDecimal("1"));
        assertEquals(10, cashLimited.quantity());

        KisAccountSnapshot.Position held = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ONE,
                new BigDecimal("80000"), BigDecimal.ZERO, BigDecimal.ZERO);
        OrderRequest exposureLimited = sizing("100000000", "10.00").capBuyQuantity(request,
                account("1000000", "1000000", List.of(held)), new BigDecimal("1"));
        assertEquals(20, exposureLimited.quantity());

        assertEquals(5, sizing("100000000", "10.00").capBuyQuantity(buy(5, "1000"),
                account("1000000", "1000000", List.of()), new BigDecimal("1")).quantity());
    }

    @Test
    void capsAtConfiguredPerOrderNotional() {
        OrderRequest result = sizing("5000", "100.00").capBuyQuantity(buy(100, "1000"),
                account("1000000", "1000000", List.of()), new BigDecimal("100"));

        assertEquals(5, result.quantity());
    }

    @Test
    void leavesRequestUnchangedWhenSizingInputsAreMissingOrNoWholeShareFits() {
        KisLiveOrderSizingService sizing = sizing("100000000", "10.00");
        OrderRequest request = buy(50, "1000");

        assertSame(request, sizing.capBuyQuantity(request, account("1000000", "1000000", List.of()), null));
        assertSame(request, sizing.capBuyQuantity(request, account(null, "1000000", List.of()),
                new BigDecimal("1000000")));
        assertSame(request, sizing.capBuyQuantity(request, account("1000000", "1000000", List.of()),
                new BigDecimal("1000000")));
    }

    private static KisLiveOrderSizingService sizing(String maxNotional, String positionPercent) {
        return new KisLiveOrderSizingService(
                new TradingSafetyProperties("paper", false, new BigDecimal(maxNotional), new BigDecimal(maxNotional)),
                new KisLiveRiskLimits(new BigDecimal("1.00"), new BigDecimal("0.25"),
                        new BigDecimal(positionPercent), 3, 3));
    }

    private static OrderRequest buy(int quantity, String price) {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, quantity,
                new BigDecimal(price), Currency.KRW, "orb_vwap_v1", "model.v1");
    }

    private static KisAccountSnapshot account(String equity, String orderable,
                                               List<KisAccountSnapshot.Position> positions) {
        return new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal(orderable),
                new BigDecimal(orderable), equity == null ? null : new BigDecimal(equity), positions,
                Instant.now(), "KIS_READ_ONLY_REST");
    }
}
