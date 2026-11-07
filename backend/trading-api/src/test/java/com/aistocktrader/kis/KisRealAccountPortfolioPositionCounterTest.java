package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.OptionalInt;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class KisRealAccountPortfolioPositionCounterTest {
    private final KisAccountClient accounts = mock(KisAccountClient.class);
    private final KisRealAccountPortfolioPositionCounter counter = new KisRealAccountPortfolioPositionCounter(accounts);

    @Test
    void countsOpenInstrumentsFromBothFreshBrokerSnapshots() {
        KisAccountSnapshot kr = account(Market.KR, "KRW", List.of(
                position("005930", "2"), position("069500", "0")));
        KisAccountSnapshot us = account(Market.US, "USD", List.of(
                position("AAPL", "1"), position("MSFT", "3")));
        when(accounts.getBalance(KisEnvironment.REAL, Market.US, null, null, "NASD")).thenReturn(us);

        OptionalInt result = counter.countOpenPositionsAcrossMarkets(kr);

        assertTrue(result.isPresent());
        assertEquals(3, result.getAsInt());
        verify(accounts).getBalance(KisEnvironment.REAL, Market.US, null, null, "NASD");
    }

    @Test
    void rejectsUnavailableOtherMarketSnapshotInsteadOfAssumingNoPositions() {
        KisAccountSnapshot kr = account(Market.KR, "KRW", List.of(position("005930", "1")));

        OptionalInt result = counter.countOpenPositionsAcrossMarkets(kr);

        assertTrue(result.isEmpty());
    }

    @Test
    void rejectsStaleOrMalformedCurrentSnapshotBeforeCallingKis() {
        KisAccountSnapshot stale = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ONE, List.of(position("005930", "1")),
                Instant.now().minusSeconds(6), "KIS_READ_ONLY_REST");

        assertTrue(counter.countOpenPositionsAcrossMarkets(stale).isEmpty());
        verifyNoInteractions(accounts);
    }

    @Test
    void rejectsDuplicateSymbolsBecausePositionCountWouldBeAmbiguous() {
        KisAccountSnapshot kr = account(Market.KR, "KRW", List.of(
                position("005930", "1"), position("005930", "2")));

        assertTrue(counter.countOpenPositionsAcrossMarkets(kr).isEmpty());
        verifyNoInteractions(accounts);
    }

    @Test
    void rejectsOtherMarketResponseWithWrongMarketIdentity() {
        KisAccountSnapshot kr = account(Market.KR, "KRW", List.of(position("005930", "1")));
        when(accounts.getBalance(KisEnvironment.REAL, Market.US, null, null, "NASD"))
                .thenReturn(account(Market.KR, "KRW", List.of()));

        assertTrue(counter.countOpenPositionsAcrossMarkets(kr).isEmpty());
    }

    private static KisAccountSnapshot account(Market market, String currency,
                                               List<KisAccountSnapshot.Position> positions) {
        return new KisAccountSnapshot(KisEnvironment.REAL, market, currency, BigDecimal.ZERO,
                BigDecimal.ZERO, BigDecimal.ONE, positions, Instant.now(), "KIS_READ_ONLY_REST");
    }

    private static KisAccountSnapshot.Position position(String symbol, String quantity) {
        return new KisAccountSnapshot.Position(symbol, symbol, new BigDecimal(quantity), BigDecimal.ZERO,
                BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ZERO, BigDecimal.ZERO);
    }
}
