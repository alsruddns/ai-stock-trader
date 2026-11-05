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
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

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
            ExecutorService workers = Executors.newFixedThreadPool(4);
            try {
                List<Future<?>> reservations = new ArrayList<>();
                for (int index = 0; index < 4; index++) {
                    reservations.add(workers.submit(() -> {
                        try {
                            limiter.acquire(KisEnvironment.REAL, appKey, Duration.ofMillis(100));
                        } catch (InterruptedException exception) {
                            Thread.currentThread().interrupt();
                            throw new IllegalStateException("integration reservation was interrupted", exception);
                        }
                    }));
                }
                for (Future<?> reservation : reservations) reservation.get();
            } finally {
                workers.shutdownNow();
            }
            assertTrue(Duration.between(started, Instant.now()).toMillis() >= 250,
                    "concurrent workers must receive distinct serialized shared slots");
            String stored = jdbc.queryForObject("SELECT app_key_sha256 FROM kis_rest_rate_limit_slot "
                    + "WHERE environment = 'REAL' AND app_key_sha256 = ?", String.class, fingerprint);
            assertEquals(fingerprint, stored.trim());
            assertNotEquals(appKey, stored.trim());
        } finally {
            jdbc.update("DELETE FROM kis_rest_rate_limit_slot WHERE environment = 'REAL' AND app_key_sha256 = ?", fingerprint);
        }
    }
}
