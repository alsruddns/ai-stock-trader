package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class KisPaperSignalOrderSizerTest {
    private final KisPaperSignalOrderSizer sizer = new KisPaperSignalOrderSizer(
            new KisLiveRiskLimits(null, null, null, 3, 3),
            new TradingSafetyProperties("paper", false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)));

    @Test
    void quantityIsCappedByRiskPositionCashAndConfiguredOrderLimit() {
        KisAccountSnapshot account = account("10000000", "1000000", List.of());

        int quantity = sizer.sizeDomesticBuy("005930", BigDecimal.valueOf(30_000),
                BigDecimal.valueOf(1_000), account);

        assertEquals(25, quantity); // 0.25% of equity / 100 KRW stop distance
    }

    @Test
    void existingSymbolPositionBlocksAdditionalEntryUntilExitCompletes() {
        KisAccountSnapshot account = account("10000000", "1000000", List.of(
                new KisAccountSnapshot.Position("005930", "Samsung", BigDecimal.ONE, BigDecimal.ONE,
                        BigDecimal.valueOf(70_000), BigDecimal.valueOf(70_000), BigDecimal.valueOf(900_000),
                        BigDecimal.ZERO, BigDecimal.ZERO)));

        assertThrows(IllegalStateException.class, () -> sizer.sizeDomesticBuy("005930",
                BigDecimal.valueOf(70_000), BigDecimal.valueOf(1_000), account));
    }

    @Test
    void invalidOrNonPaperAccountFailsClosed() {
        assertThrows(IllegalStateException.class, () -> sizer.sizeDomesticBuy("005930",
                BigDecimal.valueOf(70_000), BigDecimal.valueOf(1_000), null));
        KisAccountSnapshot real = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(1_000_000),
                List.of(), Instant.now(), "test");
        assertThrows(IllegalStateException.class, () -> sizer.sizeDomesticBuy("005930",
                BigDecimal.valueOf(70_000), BigDecimal.valueOf(1_000), real));
    }

    @Test
    void zeroQuantityUnderLimitsFailsClosed() {
        KisAccountSnapshot account = account("10000000", "1", List.of());
        assertThrows(IllegalStateException.class, () -> sizer.sizeDomesticBuy("005930",
                BigDecimal.valueOf(70_000), BigDecimal.valueOf(1_000), account));
    }

    @Test
    void newSymbolIsBlockedWhenMaximumPositionCountIsAlreadyReached() {
        List<KisAccountSnapshot.Position> positions = List.of("005930", "000660", "035720").stream()
                .map(symbol -> new KisAccountSnapshot.Position(symbol, "Holding", BigDecimal.ONE, BigDecimal.ONE,
                        BigDecimal.valueOf(10_000), BigDecimal.valueOf(10_000), BigDecimal.valueOf(10_000),
                        BigDecimal.ZERO, BigDecimal.ZERO)).toList();
        KisAccountSnapshot account = account("10000000", "1000000", positions);

        assertThrows(IllegalStateException.class, () -> sizer.sizeDomesticBuy("114800",
                BigDecimal.valueOf(10_000), BigDecimal.valueOf(1_000), account));
    }

    private static KisAccountSnapshot account(String equity, String cash,
                                               List<KisAccountSnapshot.Position> positions) {
        return new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW",
                new BigDecimal(cash), new BigDecimal(cash), new BigDecimal(equity), positions,
                Instant.now(), "test");
    }
}
