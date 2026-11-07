package com.aistocktrader.kis;

import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KisRealizedPnlCalculatorTest {
    private final KisRealizedPnlCalculator calculator = new KisRealizedPnlCalculator();
    private final KisRealizedPnlCalculator.Costs zeroCosts = costs("0", "0", "0");

    @Test
    void averageCostInventoryProducesNetPnlForEachSellAndCountsTrailingLosses() {
        var result = calculator.calculate(List.of(), List.of(
                buy("a", "AAA", 2, "10", 1), sell("b", "AAA", 1, "12", 2),
                buy("c", "AAA", 1, "14", 3), sell("d", "AAA", 1, "9", 4),
                sell("e", "AAA", 1, "9", 5)), zeroCosts);

        assertTrue(result.valid());
        assertEquals(2, result.consecutiveLosses());
        assertMoney("2", result.trades().getFirst().netPnl());
        assertMoney("-3", result.trades().get(1).netPnl());
        assertMoney("-3", result.trades().get(2).netPnl());
    }

    @Test
    void commissionsAndSellTaxCanTurnGrossProfitIntoNetLoss() {
        var result = calculator.calculate(List.of(), List.of(
                buy("a", "AAA", 1, "100", 1), sell("b", "AAA", 1, "100.1", 2)),
                costs("5", "5", "2"));

        assertTrue(result.valid());
        assertEquals(1, result.consecutiveLosses());
        assertTrue(result.trades().getFirst().netPnl().signum() < 0);
    }

    @Test
    void openingInventoryUsesBrokerAverageCostAndCannotBeOversold() {
        var result = calculator.calculate(List.of(new KisRealizedPnlCalculator.OpeningPosition(
                        "AAA", new BigDecimal("2"), new BigDecimal("10"))),
                List.of(sell("s", "AAA", 1, "12", 1)), zeroCosts);

        assertTrue(result.valid());
        assertMoney("2", result.trades().getFirst().netPnl());
        assertFalse(calculator.calculate(List.of(), List.of(sell("s", "AAA", 1, "12", 1)), zeroCosts).valid());
    }

    @Test
    void aNonLossResetsTheTrailingStreakAndInvalidRatesFailClosed() {
        var result = calculator.calculate(List.of(), List.of(
                buy("a", "AAA", 2, "10", 1), sell("b", "AAA", 1, "9", 2),
                sell("c", "AAA", 1, "11", 3)), zeroCosts);

        assertTrue(result.valid());
        assertEquals(0, result.consecutiveLosses());
        assertFalse(calculator.calculate(List.of(), List.of(), costs("", "0", "0")).valid());
        assertFalse(calculator.calculate(List.of(), List.of(), costs("1001", "0", "0")).valid());
    }

    private static KisRealizedPnlCalculator.TradeFill buy(String id, String symbol, int quantity,
                                                           String price, int second) {
        return fill(id, symbol, OrderSide.BUY, quantity, price, second);
    }
    private static KisRealizedPnlCalculator.TradeFill sell(String id, String symbol, int quantity,
                                                            String price, int second) {
        return fill(id, symbol, OrderSide.SELL, quantity, price, second);
    }
    private static KisRealizedPnlCalculator.TradeFill fill(String id, String symbol, OrderSide side,
                                                            int quantity, String price, int second) {
        return new KisRealizedPnlCalculator.TradeFill(id, symbol, side, quantity, new BigDecimal(price),
                Instant.parse("2026-11-07T00:00:00Z").plusSeconds(second));
    }
    private static KisRealizedPnlCalculator.Costs costs(String buy, String sell, String tax) {
        return new KisRealizedPnlCalculator.Costs(parse(buy), parse(sell), parse(tax));
    }
    private static BigDecimal parse(String value) { return value.isEmpty() ? null : new BigDecimal(value); }
    private static void assertMoney(String expected, BigDecimal actual) {
        assertEquals(0, new BigDecimal(expected).compareTo(actual));
    }
}
