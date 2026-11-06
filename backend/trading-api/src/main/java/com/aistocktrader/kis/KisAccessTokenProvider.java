package com.aistocktrader.kis;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Shares per-environment KIS tokens across Spring account, paper-order, and REAL adapters. */
@Component
public class KisAccessTokenProvider {
    private static final String TOKEN_PATH = "/oauth2/tokenP";
    private static final ObjectMapper ERROR_MAPPER = new ObjectMapper();
    private final KisProperties properties;
    private final RestClient http;
    private final KisRestRateLimiter rateLimiter;
    private final Map<TokenKey, CachedToken> tokens = new ConcurrentHashMap<>();
    private final Map<TokenKey, Object> locks = new ConcurrentHashMap<>();

    public KisAccessTokenProvider(KisProperties properties, RestClient.Builder builder) {
        this(properties, builder, new KisRestRateLimiter());
    }

    @Autowired
    public KisAccessTokenProvider(KisProperties properties, RestClient.Builder builder,
                                  KisRestRateLimiter rateLimiter) {
        this.properties = properties;
        this.http = builder.build();
        this.rateLimiter = rateLimiter;
    }

    public String accessToken(KisEnvironment environment) {
        KisProperties.Account credentials = properties.credentials(environment);
        if (!credentials.configured()) {
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                    "KIS credentials are not configured for the selected environment.");
        }
        TokenKey key = new TokenKey(environment, fingerprint(credentials));
        CachedToken cached = tokens.get(key);
        if (valid(cached)) return cached.value();
        synchronized (locks.computeIfAbsent(key, ignored -> new Object())) {
            cached = tokens.get(key);
            if (valid(cached)) return cached.value();
            try {
                rateLimiter.acquireToken(environment, credentials.appKey());
                JsonNode response = http.post().uri(environment.baseUrl() + TOKEN_PATH)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(Map.of("grant_type", "client_credentials", "appkey", credentials.appKey(),
                                "appsecret", credentials.appSecret()))
                        .retrieve().body(JsonNode.class);
                String token = response == null ? "" : response.path("access_token").asText();
                String expiry = response == null ? "" : response.path("access_token_token_expired").asText();
                if (token.isBlank() || expiry.isBlank()) {
                    String code = safeCode(response == null ? "unknown" : response.path("msg_cd").asText("unknown"));
                    throw new KisApiException(HttpStatus.BAD_GATEWAY,
                            "KIS authentication response was incomplete (code " + code + ").");
                }
                Instant expiresAt = LocalDateTime.parse(expiry.replace(" ", "T"))
                        .toInstant(ZoneOffset.ofHours(9));
                tokens.put(key, new CachedToken(token, expiresAt));
                return token;
            } catch (KisApiException exception) {
                throw exception;
            } catch (RestClientResponseException exception) {
                String code = safeCode(errorCode(exception.getResponseBodyAsString()));
                throw new KisApiException(exception.getStatusCode().value() == 429
                        ? HttpStatus.TOO_MANY_REQUESTS : HttpStatus.BAD_GATEWAY,
                        "KIS authentication request was rejected (HTTP " + exception.getStatusCode().value()
                                + ", code " + code + ").");
            } catch (ResourceAccessException exception) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS authentication service is unreachable.");
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS authentication was interrupted.");
            } catch (RuntimeException exception) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS authentication failed; check the selected environment and credentials.");
            }
        }
    }

    private static boolean valid(CachedToken token) {
        return token != null && token.expiresAt().isAfter(Instant.now().plusSeconds(300));
    }

    private static String fingerprint(KisProperties.Account credentials) {
        try {
            byte[] bytes = (credentials.appKey() + "\u0000" + credentials.appSecret())
                    .getBytes(StandardCharsets.UTF_8);
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable.", exception);
        }
    }

    private static String safeCode(String code) {
        return code != null && code.matches("[A-Za-z0-9_-]{1,32}") ? code : "unknown";
    }

    private static String errorCode(String body) {
        try {
            JsonNode payload = ERROR_MAPPER.readTree(body);
            return payload == null ? "unknown" : payload.path("msg_cd").asText("unknown");
        } catch (Exception ignored) {
            return "unknown";
        }
    }

    private record TokenKey(KisEnvironment environment, String credentialFingerprint) { }
    private record CachedToken(String value, Instant expiresAt) { }
}
