package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Opt-in PostgreSQL verification for immutable REAL account opening inventory. */
@EnabledIfEnvironmentVariable(named = "RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisRealSessionPositionBaselinePostgresIntegrationTest {
    private static final LocalDate TEST_SESSION = LocalDate.of(1998, 1, 5);
    private static final String FINGERPRINT_A = "d".repeat(64);
    private static final String FINGERPRINT_B = "e".repeat(64);

    @Autowired private KisRealSessionPositionBaselineStore store;
    @Autowired private JdbcTemplate jdbc;

    @Test
    void storesImmutablePositionsAndAllowsAnExplicitEmptyOpeningInventory() {
        deleteTestRows();
        try {
            var holding = position("005930", "3", "71000");
            assertTrue(store.recordOnce(Market.KR, TEST_SESSION, "KRW", FINGERPRINT_A,
                    List.of(holding), Instant.parse("2026-01-01T00:00:00Z")));
            assertFalse(store.recordOnce(Market.KR, TEST_SESSION, "KRW", FINGERPRINT_A,
                    List.of(position("005930", "1", "50000")), Instant.parse("2026-01-01T00:01:00Z")));

            var baseline = store.get(Market.KR, TEST_SESSION, FINGERPRINT_A).orElseThrow();
            assertEquals(1, baseline.positionCount());
            assertEquals("005930", baseline.positions().getFirst().symbol());
            assertEquals(0, new BigDecimal("3").compareTo(baseline.positions().getFirst().quantity()));
            assertEquals(0, new BigDecimal("71000").compareTo(
                    baseline.positions().getFirst().averagePurchasePrice()));

            assertTrue(store.recordOnce(Market.KR, TEST_SESSION, "KRW", FINGERPRINT_B,
                    List.of(), Instant.parse("2026-01-01T00:00:00Z")));
            var empty = store.get(Market.KR, TEST_SESSION, FINGERPRINT_B).orElseThrow();
            assertEquals(0, empty.positionCount());
            assertTrue(empty.positions().isEmpty());
            assertFalse(store.recordOnce(Market.KR, TEST_SESSION, "KRW", "bad-fingerprint",
                    List.of(holding), Instant.now()));
        } finally {
            deleteTestRows();
        }
    }

    private static KisAccountSnapshot.Position position(String symbol, String quantity, String averagePrice) {
        return new KisAccountSnapshot.Position(symbol, "test", new BigDecimal(quantity), new BigDecimal(quantity),
                new BigDecimal(averagePrice), new BigDecimal(averagePrice), null, null, null);
    }

    private void deleteTestRows() {
        jdbc.update("DELETE FROM kis_real_session_position_baseline WHERE market = 'KR' AND session_date = ?",
                TEST_SESSION);
    }
}
