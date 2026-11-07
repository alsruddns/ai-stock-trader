package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
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
    void concurrentAdaptersShareOneTokenIssuanceRequest() throws Exception {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        CountDownLatch requestEntered = new CountDownLatch(1);
        CountDownLatch releaseResponse = new CountDownLatch(1);
        CountDownLatch workersReady = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        AtomicReference<Thread> firstThread = new AtomicReference<>();
        AtomicReference<Thread> secondThread = new AtomicReference<>();
        AtomicReference<Thread> requestThread = new AtomicReference<>();
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(request -> {
                    requestThread.set(Thread.currentThread());
                    requestEntered.countDown();
                    try {
                        if (!releaseResponse.await(5, TimeUnit.SECONDS)) {
                            throw new IOException("Timed out waiting to release the mocked KIS token response.");
                        }
                    } catch (InterruptedException exception) {
                        Thread.currentThread().interrupt();
                        throw new IOException("Interrupted while holding the mocked KIS token response.", exception);
                    }
                    return withSuccess("""
                            {"access_token":"paper-token","access_token_token_expired":"2099-11-06 23:00:00"}
                            """, MediaType.APPLICATION_JSON).createResponse(request);
                });
        KisAccessTokenProvider provider = new KisAccessTokenProvider(properties(), builder,
                new KisRestRateLimiter());
        try (ExecutorService workers = Executors.newFixedThreadPool(2)) {
            Future<String> first = workers.submit(() -> {
                firstThread.set(Thread.currentThread());
                workersReady.countDown();
                start.await();
                return provider.accessToken(KisEnvironment.PAPER);
            });
            Future<String> second = workers.submit(() -> {
                secondThread.set(Thread.currentThread());
                workersReady.countDown();
                start.await();
                return provider.accessToken(KisEnvironment.PAPER);
            });

            assertTrue(workersReady.await(5, TimeUnit.SECONDS));
            start.countDown();
            assertTrue(requestEntered.await(5, TimeUnit.SECONDS));
            Thread responseWorker = requestThread.get();
            assertNotNull(responseWorker);
            // The request callback runs on one worker. Hold it there until the other worker
            // is waiting on the provider's per-token monitor, proving true overlap.
            Thread tokenWorker = responseWorker == firstThread.get() ? secondThread.get() : firstThread.get();
            assertTrue(awaitBlocked(tokenWorker, Duration.ofSeconds(5)));
            releaseResponse.countDown();
            assertEquals("paper-token", first.get());
            assertEquals("paper-token", second.get());
        } finally {
            releaseResponse.countDown();
        }
        server.verify();
    }

    private static boolean awaitBlocked(Thread thread, Duration timeout) throws InterruptedException {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (System.nanoTime() < deadline) {
            if (thread.getState() == Thread.State.BLOCKED) return true;
            Thread.sleep(1);
        }
        return thread.getState() == Thread.State.BLOCKED;
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
