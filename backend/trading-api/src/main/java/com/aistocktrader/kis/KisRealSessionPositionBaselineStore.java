package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;

/** Immutable opening inventory and broker-reported average cost for a REAL account session. */
@Repository
public class KisRealSessionPositionBaselineStore {
    private final JdbcTemplate jdbc;

    public KisRealSessionPositionBaselineStore(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public boolean exists(Market market, LocalDate sessionDate, String accountFingerprint) {
        Boolean exists = jdbc.queryForObject("""
                SELECT EXISTS(SELECT 1 FROM kis_real_session_position_baseline
                              WHERE market = ? AND session_date = ? AND account_fingerprint = ?)
                """, Boolean.class, market.name(), sessionDate, accountFingerprint);
        return Boolean.TRUE.equals(exists);
    }

    /** First complete capture wins; malformed or duplicate positions cannot seed a cost basis. */
    @Transactional
    public boolean recordOnce(Market market, LocalDate sessionDate, String currency, String accountFingerprint,
                              List<KisAccountSnapshot.Position> positions, Instant capturedAt) {
        if (!valid(market, sessionDate, currency, accountFingerprint, positions, capturedAt)) return false;
        int inserted = jdbc.update("""
                INSERT INTO kis_real_session_position_baseline
                    (market, session_date, account_fingerprint, currency, captured_at, position_count, source)
                VALUES (?, ?, ?, ?, ?, ?, 'KIS_READ_ONLY_REST')
                ON CONFLICT (market, session_date, account_fingerprint) DO NOTHING
                """, market.name(), sessionDate, accountFingerprint, currency,
                OffsetDateTime.ofInstant(capturedAt, ZoneOffset.UTC), positions.size());
        if (inserted == 0) return false;
        if (!positions.isEmpty()) {
            jdbc.batchUpdate("""
                    INSERT INTO kis_real_session_position_baseline_item
                        (market, session_date, account_fingerprint, symbol, quantity, average_purchase_price)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, positions, positions.size(), (statement, position) -> {
                statement.setString(1, market.name());
                statement.setObject(2, sessionDate);
                statement.setString(3, accountFingerprint);
                statement.setString(4, position.symbol().trim().toUpperCase(java.util.Locale.ROOT));
                statement.setBigDecimal(5, position.quantity());
                statement.setBigDecimal(6, position.averagePrice());
            });
        }
        return true;
    }

    public Optional<PositionBaseline> get(Market market, LocalDate sessionDate, String accountFingerprint) {
        List<Position> positions = jdbc.query("""
                SELECT symbol, quantity, average_purchase_price
                  FROM kis_real_session_position_baseline_item
                 WHERE market = ? AND session_date = ? AND account_fingerprint = ?
                 ORDER BY symbol
                """, (row, index) -> new Position(row.getString(1), row.getBigDecimal(2), row.getBigDecimal(3)),
                market.name(), sessionDate, accountFingerprint);
        Optional<PositionBaseline> baseline = jdbc.query("""
                SELECT currency, captured_at, position_count, source
                  FROM kis_real_session_position_baseline
                 WHERE market = ? AND session_date = ? AND account_fingerprint = ?
                """, result -> result.next()
                        ? Optional.of(new PositionBaseline(market, sessionDate, accountFingerprint,
                        result.getString(1), result.getTimestamp(2).toInstant(), result.getInt(3),
                        result.getString(4), positions))
                        : Optional.empty(), market.name(), sessionDate, accountFingerprint);
        return baseline.filter(value -> value.positionCount() == value.positions().size());
    }

    private static boolean valid(Market market, LocalDate sessionDate, String currency, String fingerprint,
                                 List<KisAccountSnapshot.Position> positions, Instant capturedAt) {
        if (market == null || sessionDate == null || positions == null || capturedAt == null
                || capturedAt.isAfter(Instant.now()) || fingerprint == null || !fingerprint.matches("[0-9a-f]{64}")) {
            return false;
        }
        if (!(market == Market.KR && "KRW".equals(currency))
                && !(market == Market.US && "USD".equals(currency))) return false;
        HashSet<String> symbols = new HashSet<>();
        for (KisAccountSnapshot.Position position : positions) {
            if (position == null || position.symbol() == null || position.symbol().isBlank()
                    || position.symbol().length() > 24 || position.quantity() == null
                    || position.quantity().signum() <= 0 || position.averagePrice() == null
                    || position.averagePrice().signum() <= 0
                    || !symbols.add(position.symbol().trim().toUpperCase(java.util.Locale.ROOT))) return false;
        }
        return true;
    }

    public record Position(String symbol, BigDecimal quantity, BigDecimal averagePurchasePrice) { }
    public record PositionBaseline(Market market, LocalDate sessionDate, String accountFingerprint,
                                   String currency, Instant capturedAt, int positionCount,
                                   String source, List<Position> positions) { }
}
