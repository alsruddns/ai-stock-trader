package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.time.Duration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;

class KisAccessTokenProviderTest {
    private static final String PAPER_URL = "https://openapivts.koreainvestment.com:29443";

    @Test
    void reusesOneEnvironmentTokenForAllAdaptersSharingTheProvider() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess("""
                        {"access_token":"paper-token","access_token_token_expired":"2099-11-06 23:00:00"}
                        """, MediaType.APPLICATION_JSON));
        KisAccessTokenProvider provider = new KisAccessTokenProvider(properties(), builder,
                new KisRestRateLimiter());

        assertEquals("paper-token", provider.accessToken(KisEnvironment.PAPER));
        assertEquals("paper-token", provider.accessToken(KisEnvironment.PAPER));
        server.verify();
    }

    @Test
    void reportsOnlySafeBrokerCodeWhenTokenResponseIsRejected() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess("""
                        {"rt_cd":"1","msg_cd":"EGW00123","msg1":"private detail"}
                        """, MediaType.APPLICATION_JSON));
        KisAccessTokenProvider provider = new KisAccessTokenProvider(properties(), builder,
                new KisRestRateLimiter());

        KisApiException error = assertThrows(KisApiException.class,
                () -> provider.accessToken(KisEnvironment.PAPER));

        assertEquals("KIS authentication response was incomplete (code EGW00123).", error.getMessage());
        assertFalse(error.getMessage().contains("private detail"));
        server.verify();
    }

    @Test
    void extractsOnlyTheSafeBrokerCodeFromAnHttp403Body() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withStatus(HttpStatus.FORBIDDEN).contentType(MediaType.APPLICATION_JSON).body("""
                        {"msg_cd":"EGW00123","msg1":"private detail","access_token":"do-not-leak"}
                        """));
        KisAccessTokenProvider provider = new KisAccessTokenProvider(properties(), builder,
                new KisRestRateLimiter());

        KisApiException error = assertThrows(KisApiException.class,
                () -> provider.accessToken(KisEnvironment.PAPER));

        assertEquals("KIS authentication request was rejected (HTTP 403, code EGW00123).", error.getMessage());
        assertFalse(error.getMessage().contains("private detail"));
        assertFalse(error.getMessage().contains("do-not-leak"));
        server.verify();
    }

    @Test
    void extractsOnlyTheSafeOAuthErrorCodeFromAnHttp403Body() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withStatus(HttpStatus.FORBIDDEN).contentType(MediaType.APPLICATION_JSON).body("""
                        {"error_code":"EGW00002","error_description":"private detail","appkey":"do-not-leak"}
                        """));
        KisAccessTokenProvider provider = new KisAccessTokenProvider(properties(), builder,
                new KisRestRateLimiter());

        KisApiException error = assertThrows(KisApiException.class,
                () -> provider.accessToken(KisEnvironment.PAPER));

        assertEquals("KIS authentication request was rejected (HTTP 403, code EGW00002).", error.getMessage());
        assertFalse(error.getMessage().contains("private detail"));
        assertFalse(error.getMessage().contains("do-not-leak"));
        server.verify();
    }

    private static KisProperties properties() {
        return new KisProperties(true, true, false, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "8765432101"),
                Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofMillis(100));
    }
}
