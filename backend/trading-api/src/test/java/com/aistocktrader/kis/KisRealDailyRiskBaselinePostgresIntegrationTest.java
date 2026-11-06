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
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@EnabledIfEnvironmentVariable(named = "RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", matches = "true")
class KisRealDailyRiskBaselinePostgresIntegrationTest {
    private static final LocalDate ISOLATED_SESSION = LocalDate.of(1999, 1, 4);
    private static final String FINGERPRINT_A = "a".repeat(64);
    private static final String FINGERPRINT_B = "b".repeat(64);

    @Autowired private KisRealDailyRiskBaselineStore baselines;
    @Autowired private JdbcTemplate jdbc;

    @Test
    void baselineIsImmutableAndScopedToAccountFingerprint() {
        deleteTestRows();
        try {
            baselines.recordOnce(Market.KR, ISOLATED_SESSION, "KRW", FINGERPRINT_A,
                    new BigDecimal("1000000"), Instant.parse("2026-01-01T00:00:00Z"));
            baselines.recordOnce(Market.KR, ISOLATED_SESSION, "KRW", FINGERPRINT_A,
                    new BigDecimal("900000"), Instant.parse("2026-01-01T00:01:00Z"));
            baselines.recordOnce(Market.KR, ISOLATED_SESSION, "KRW", FINGERPRINT_B,
                    new BigDecimal("500000"), Instant.parse("2026-01-01T00:00:00Z"));

            assertTrue(baselines.exists(Market.KR, ISOLATED_SESSION, FINGERPRINT_A));
            assertEquals(0, baselines.equity(Market.KR, ISOLATED_SESSION, FINGERPRINT_A)
                    .orElseThrow().compareTo(new BigDecimal("1000000")));
            assertEquals(Optional.of(new BigDecimal("500000.000000")),
                    baselines.equity(Market.KR, ISOLATED_SESSION, FINGERPRINT_B));
            assertFalse(baselines.exists(Market.KR, ISOLATED_SESSION, "c".repeat(64)));
        } finally {
            deleteTestRows();
        }
    }

    private void deleteTestRows() {
        jdbc.update("DELETE FROM kis_real_daily_risk_baseline WHERE market = 'KR' AND session_date = ?",
                ISOLATED_SESSION);
    }
}
