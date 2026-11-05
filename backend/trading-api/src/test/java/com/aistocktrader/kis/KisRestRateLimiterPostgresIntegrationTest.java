package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@EnabledIfEnvironmentVariable(named = "RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", matches = "true")
class KisRestRateLimiterPostgresIntegrationTest {
    @Autowired private KisRestRateLimiter limiter;
    @Autowired private JdbcTemplate jdbc;

    @Test
    void reservesConsecutiveRealSlotsAndPersistsOnlyAppKeyFingerprint() throws Exception {
        String appKey = "local-integration-only-java-shared-rate-limit";
        String fingerprint = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(appKey.getBytes(StandardCharsets.UTF_8)));
        jdbc.update("DELETE FROM kis_rest_rate_limit_slot WHERE environment = 'REAL' AND app_key_sha256 = ?", fingerprint);
        try {
            Instant started = Instant.now();
            limiter.acquire(KisEnvironment.REAL, appKey, Duration.ofMillis(100));
            limiter.acquire(KisEnvironment.REAL, appKey, Duration.ofMillis(100));
            assertTrue(Duration.between(started, Instant.now()).toMillis() >= 85,
                    "second reservation must wait for the configured shared slot");
            String stored = jdbc.queryForObject("SELECT app_key_sha256 FROM kis_rest_rate_limit_slot "
                    + "WHERE environment = 'REAL' AND app_key_sha256 = ?", String.class, fingerprint);
            assertEquals(fingerprint, stored.trim());
            assertNotEquals(appKey, stored.trim());
        } finally {
            jdbc.update("DELETE FROM kis_rest_rate_limit_slot WHERE environment = 'REAL' AND app_key_sha256 = ?", fingerprint);
        }
    }
}
