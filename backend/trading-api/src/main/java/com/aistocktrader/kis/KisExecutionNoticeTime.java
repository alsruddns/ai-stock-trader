package com.aistocktrader.kis;

import com.aistocktrader.order.Market;

import java.time.Instant;
import java.time.ZoneId;
import java.util.Optional;

/** Resolves an exchange-local KIS notice time only against fresh session evidence. */
final class KisExecutionNoticeTime {
    private KisExecutionNoticeTime() { }

    static Optional<Instant> resolve(KisExecutionNoticeParser.ExecutionNotice notice,
                                     KisMarketSessionClient.MarketSession session, Instant now) {
        if (notice == null || now == null || notice.fillTime() == null
                || !KisRealDailyRiskBaselineService.freshSession(session, notice.market(), now)
                || !session.session() || session.openAt() == null || session.closeAt() == null) {
            return Optional.empty();
        }
        ZoneId zone = notice.market() == Market.KR
                ? ZoneId.of("Asia/Seoul") : ZoneId.of("America/New_York");
        Instant fillAt = session.sessionDate().atTime(notice.fillTime()).atZone(zone).toInstant();
        if (fillAt.isBefore(session.openAt()) || fillAt.isAfter(session.closeAt())) return Optional.empty();
        return Optional.of(fillAt);
    }
}
