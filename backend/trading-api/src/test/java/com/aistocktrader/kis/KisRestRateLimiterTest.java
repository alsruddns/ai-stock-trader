package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.http.HttpStatus;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.HexFormat;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRestRateLimiterTest {
    @Test
    void reservesIndependentConservativeTokenIssueSlotUsingOnlyAppKeyFingerprint() throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject(anyString(), eq(Timestamp.class), any(Object[].class)))
                .thenReturn(Timestamp.from(Instant.now().minusSeconds(1)));
        KisRestRateLimiter limiter = new KisRestRateLimiter(jdbc);

        limiter.acquireToken(KisEnvironment.PAPER, "local-test-app-key");

        var captor = org.mockito.ArgumentCaptor.forClass(Object[].class);
        verify(jdbc).queryForObject(contains("kis_token_issue_rate_limit_slot"),
                eq(Timestamp.class), captor.capture());
        Object[] parameters = captor.getValue();
        assertArrayEquals(new Object[]{"PAPER", sha256("local-test-app-key")}, parameters);
        verify(jdbc, never()).queryForObject(contains("kis_rest_rate_limit_slot"),
                eq(Timestamp.class), any(Object[].class));
    }

    @Test
    void reservesSharedPostgresSlotUsingOnlyAppKeyFingerprint() throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject(anyString(), eq(Timestamp.class), any(Object[].class)))
                .thenReturn(Timestamp.from(Instant.now().minusSeconds(1)));
        KisRestRateLimiter limiter = new KisRestRateLimiter(jdbc);

        limiter.acquire(KisEnvironment.PAPER, "local-test-app-key", java.time.Duration.ofMillis(100));

        var captor = org.mockito.ArgumentCaptor.forClass(Object[].class);
        verify(jdbc).queryForObject(contains("ON CONFLICT (environment, app_key_sha256) DO UPDATE"),
                eq(Timestamp.class), captor.capture());
        Object[] parameters = captor.getValue();
        assertEquals("PAPER", parameters[0]);
        assertEquals(sha256("local-test-app-key"), parameters[1]);
        assertEquals(1.0, (Double) parameters[2]);
        assertEquals(1.0, (Double) parameters[3]);
        assertEquals(1.0, (Double) parameters[4]);
        assertFalse(parameters.toString().contains("local-test-app-key"));
    }

    @Test
    void databaseFailureStopsBeforeRequestAndReturnsSafeServiceUnavailable() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.queryForObject(anyString(), eq(Timestamp.class), any(Object[].class)))
                .thenThrow(new DataAccessResourceFailureException("secret database detail"));
        KisRestRateLimiter limiter = new KisRestRateLimiter(jdbc);

        KisApiException error = assertThrows(KisApiException.class,
                () -> limiter.acquire(KisEnvironment.REAL, "local-test-app-key", java.time.Duration.ofMillis(1)));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.status());
        assertEquals("KIS shared rate-limit service is unavailable.", error.getMessage());
        assertFalse(error.getMessage().contains("secret database detail"));
    }

    private static String sha256(String value) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8)));
    }
}
