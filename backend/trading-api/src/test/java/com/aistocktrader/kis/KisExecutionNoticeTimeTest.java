package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;

class KisExecutionNoticeTimeTest {
    @Test
    void mapsDomesticExchangeTimeUsingTheSessionDateAndSeoulZone() {
        Instant now = Instant.parse("2026-11-09T01:00:00Z");
        var session = new KisMarketSessionClient.MarketSession(Market.KR, LocalDate.parse("2026-11-09"),
                true, true, Instant.parse("2026-11-09T00:00:00Z"), Instant.parse("2026-11-09T06:30:00Z"), now);

        assertEquals(Optional.of(Instant.parse("2026-11-09T00:15:01Z")),
                KisExecutionNoticeTime.resolve(notice(Market.KR, LocalTime.of(9, 15, 1)), session, now));
    }

    @Test
    void mapsUsExchangeTimeWithTheSessionDateAndDaylightSavingRules() {
        Instant now = Instant.parse("2026-11-06T15:00:00Z");
        var session = new KisMarketSessionClient.MarketSession(Market.US, LocalDate.parse("2026-11-06"),
                true, true, Instant.parse("2026-11-06T14:30:00Z"), Instant.parse("2026-11-06T21:00:00Z"), now);

        assertEquals(Optional.of(Instant.parse("2026-11-06T14:30:12Z")),
                KisExecutionNoticeTime.resolve(notice(Market.US, LocalTime.of(9, 30, 12)), session, now));
    }

    @Test
    void rejectsStaleOrOutOfSessionCalendarEvidence() {
        Instant now = Instant.parse("2026-11-06T15:00:00Z");
        var stale = session(Market.US, now.minusSeconds(31));
        var fresh = session(Market.US, now);

        assertEquals(Optional.empty(), KisExecutionNoticeTime.resolve(
                notice(Market.US, LocalTime.of(10, 0)), stale, now));
        assertEquals(Optional.empty(), KisExecutionNoticeTime.resolve(
                notice(Market.US, LocalTime.of(8, 0)), fresh, now));
    }

    private static KisMarketSessionClient.MarketSession session(Market market, Instant evaluatedAt) {
        return new KisMarketSessionClient.MarketSession(market, LocalDate.parse("2026-11-06"),
                true, true, Instant.parse("2026-11-06T14:30:00Z"), Instant.parse("2026-11-06T21:00:00Z"), evaluatedAt);
    }

    private static KisExecutionNoticeParser.ExecutionNotice notice(Market market, LocalTime fillTime) {
        return new KisExecutionNoticeParser.ExecutionNotice(market, "ORDER01",
                market == Market.KR ? "005930" : "AAPL", OrderSide.BUY, 1, BigDecimal.ONE, fillTime);
    }
}
