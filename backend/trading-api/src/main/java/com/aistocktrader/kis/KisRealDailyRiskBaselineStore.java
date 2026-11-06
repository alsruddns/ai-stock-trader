package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Optional;

@Repository
public class KisRealDailyRiskBaselineStore {
    private final JdbcTemplate jdbc;

    public KisRealDailyRiskBaselineStore(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<BigDecimal> equity(Market market, LocalDate sessionDate, String accountFingerprint) {
        return jdbc.query("""
                SELECT account_equity FROM kis_real_daily_risk_baseline
                 WHERE market = ? AND session_date = ? AND account_fingerprint = ?
                """, result -> result.next() ? Optional.of(result.getBigDecimal(1)) : Optional.empty(),
                market.name(), sessionDate, accountFingerprint);
    }

    public boolean exists(Market market, LocalDate sessionDate, String accountFingerprint) {
        Boolean exists = jdbc.queryForObject("""
                SELECT EXISTS(SELECT 1 FROM kis_real_daily_risk_baseline
                              WHERE market = ? AND session_date = ? AND account_fingerprint = ?)
                """, Boolean.class, market.name(), sessionDate, accountFingerprint);
        return Boolean.TRUE.equals(exists);
    }

    /** The first verified opening-session baseline wins; subsequent captures cannot reset the limit. */
    public void recordOnce(Market market, LocalDate sessionDate, String currency, String accountFingerprint,
                           BigDecimal equity, Instant capturedAt) {
        jdbc.update("""
                INSERT INTO kis_real_daily_risk_baseline
                    (market, currency, account_fingerprint, session_date, account_equity, captured_at, source)
                VALUES (?, ?, ?, ?, ?, ?, 'KIS_READ_ONLY_REST')
                ON CONFLICT (market, session_date, account_fingerprint) DO NOTHING
                """, market.name(), currency, accountFingerprint, sessionDate, equity,
                OffsetDateTime.ofInstant(capturedAt, ZoneOffset.UTC));
    }
}
