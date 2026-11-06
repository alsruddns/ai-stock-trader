package com.aistocktrader.kis;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** Persists cumulative broker fill observations so restarts do not erase partial-fill history. */
@Repository
public class KisRealFillSnapshotStore {
    private final JdbcTemplate jdbc;

    public KisRealFillSnapshotStore(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void record(KisOrderIntent intent, KisBrokerOrderSnapshot snapshot) {
        if (intent == null || snapshot == null || intent.getEnvironment() != KisEnvironment.REAL
                || snapshot.filledQuantity() <= 0 || snapshot.remainingQuantity() < 0
                || snapshot.filledQuantity() + snapshot.remainingQuantity() > intent.getQuantity()
                || snapshot.averageFillPrice() == null || snapshot.averageFillPrice().signum() <= 0) return;
        Integer previousFill = intent.getFilledQuantity();
        if (previousFill != null && snapshot.filledQuantity() < previousFill) return;
        if (previousFill != null && snapshot.filledQuantity() == previousFill
                && snapshot.averageFillPrice().compareTo(intent.getAverageFillPrice() == null
                        ? BigDecimal.ZERO : intent.getAverageFillPrice()) == 0) return;

        jdbc.update("""
                INSERT INTO kis_real_fill_snapshot
                    (idempotency_key, cumulative_filled_quantity, cumulative_average_fill_price,
                     remaining_quantity, observed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (idempotency_key, cumulative_filled_quantity) DO UPDATE
                   SET cumulative_average_fill_price = EXCLUDED.cumulative_average_fill_price,
                       remaining_quantity = EXCLUDED.remaining_quantity,
                       observed_at = EXCLUDED.observed_at
                """, intent.getIdempotencyKey(), snapshot.filledQuantity(), snapshot.averageFillPrice(),
                snapshot.remainingQuantity(), java.sql.Timestamp.from(Instant.now()));
    }

    public List<FillSnapshot> forIntent(UUID idempotencyKey) {
        return jdbc.query("""
                SELECT cumulative_filled_quantity, cumulative_average_fill_price,
                       remaining_quantity, observed_at
                  FROM kis_real_fill_snapshot
                 WHERE idempotency_key = ?
                 ORDER BY observed_at ASC, cumulative_filled_quantity ASC
                """, (row, index) -> new FillSnapshot(idempotencyKey, row.getInt(1), row.getBigDecimal(2),
                row.getInt(3), row.getTimestamp(4).toInstant()), idempotencyKey);
    }

    public record FillSnapshot(UUID idempotencyKey, int cumulativeFilledQuantity,
                               BigDecimal cumulativeAverageFillPrice, int remainingQuantity,
                               Instant observedAt) { }
}
