package com.aistocktrader.kis;

import org.springframework.dao.DataAccessException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** PostgreSQL-backed KIS pacing shared by Spring and Python, keyed by a one-way app-key fingerprint. */
@Component
final class KisRestRateLimiter {
    private static final long PAPER_FLOOR_MILLIS = 1_000;
    private static final long REAL_FLOOR_MILLIS = 1_000;
    private static final long TOKEN_ISSUE_FLOOR_MILLIS = 60_000;
    private static final long LOCAL_TOKEN_ISSUE_FLOOR_MILLIS = 1_000;
    private static final String RESERVE_SLOT_SQL = """
            INSERT INTO kis_rest_rate_limit_slot (environment, app_key_sha256, next_request_at)
            VALUES (?, ?, clock_timestamp() + interval '1 second' * ?)
            ON CONFLICT (environment, app_key_sha256) DO UPDATE
            SET next_request_at = GREATEST(kis_rest_rate_limit_slot.next_request_at, clock_timestamp())
                                     + interval '1 second' * ?,
                updated_at = clock_timestamp()
            RETURNING next_request_at - interval '1 second' * ? AS reserved_at
            """;
    private static final String RESERVE_TOKEN_SLOT_SQL = """
            INSERT INTO kis_token_issue_rate_limit_slot (environment, app_key_sha256, next_request_at)
            VALUES (?, ?, clock_timestamp() + interval '1 minute')
            ON CONFLICT (environment, app_key_sha256) DO UPDATE
            SET next_request_at = GREATEST(kis_token_issue_rate_limit_slot.next_request_at, clock_timestamp())
                                     + interval '1 minute',
                updated_at = clock_timestamp()
            RETURNING next_request_at - interval '1 minute' AS reserved_at
            """;
    private static final Map<String, Object> LOCKS = new ConcurrentHashMap<>();
    private static final Map<String, Instant> NEXT_SLOT = new ConcurrentHashMap<>();
    private final JdbcTemplate jdbc;

    @Autowired
    KisRestRateLimiter(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    /** In-memory fallback is only used by tests that construct clients directly. */
    KisRestRateLimiter() { this.jdbc = null; }

    void acquire(KisEnvironment environment, String appKey, Duration configuredMinimum) throws InterruptedException {
        if (appKey == null || appKey.isBlank()) {
            throw unavailable();
        }
        long floor = environment == KisEnvironment.PAPER ? PAPER_FLOOR_MILLIS : REAL_FLOOR_MILLIS;
        long configured = configuredMinimum == null ? 0 : configuredMinimum.toMillis();
        long intervalMillis = Math.max(configured, floor);
        if (jdbc == null) {
            acquireLocally(environment, appKey, intervalMillis);
            return;
        }
        String fingerprint = sha256(appKey);
        final Timestamp reserved;
        try {
            reserved = jdbc.queryForObject(RESERVE_SLOT_SQL, Timestamp.class,
                    environment.name(), fingerprint, intervalMillis / 1_000.0,
                    intervalMillis / 1_000.0, intervalMillis / 1_000.0);
        } catch (DataAccessException exception) {
            throw unavailable();
        }
        if (reserved == null) throw unavailable();
        long delayMillis = Duration.between(Instant.now(), reserved.toInstant()).toMillis();
        if (delayMillis > 0) Thread.sleep(delayMillis);
    }

    /** Token requests share a conservative one-per-minute slot across both services and environments. */
    void acquireToken(KisEnvironment environment, String appKey) throws InterruptedException {
        if (appKey == null || appKey.isBlank()) throw unavailable();
        String fingerprint = sha256(appKey);
        if (jdbc == null) {
            // Local fallback exists only for directly constructed tests; production always uses shared DB pacing.
            acquireLocally(environment, "token:" + appKey, LOCAL_TOKEN_ISSUE_FLOOR_MILLIS);
            return;
        }
        final Timestamp reserved;
        try {
            reserved = jdbc.queryForObject(RESERVE_TOKEN_SLOT_SQL, Timestamp.class,
                    environment.name(), fingerprint);
        } catch (DataAccessException exception) {
            throw unavailable();
        }
        if (reserved == null) throw unavailable();
        long delayMillis = Duration.between(Instant.now(), reserved.toInstant()).toMillis();
        if (delayMillis > 0) Thread.sleep(delayMillis);
    }

    private void acquireLocally(KisEnvironment environment, String appKey, long intervalMillis) throws InterruptedException {
        String key = environment.name() + ":" + sha256(appKey);
        synchronized (LOCKS.computeIfAbsent(key, ignored -> new Object())) {
            Instant now = Instant.now();
            Instant slot = NEXT_SLOT.getOrDefault(key, now);
            if (slot.isAfter(now)) Thread.sleep(Duration.between(now, slot).toMillis());
            NEXT_SLOT.put(key, (slot.isAfter(now) ? slot : now).plusMillis(intervalMillis));
        }
    }

    private static String sha256(String appKey) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(appKey.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static KisApiException unavailable() {
        return new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                "KIS shared rate-limit service is unavailable.");
    }
}
