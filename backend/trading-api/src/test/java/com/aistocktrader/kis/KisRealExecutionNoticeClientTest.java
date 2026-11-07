package com.aistocktrader.kis;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;

import java.time.Duration;

import static org.mockito.Mockito.*;

class KisRealExecutionNoticeClientTest {
    @Test
    void doesNotRequestApprovalOrConnectWhenDisabled() {
        RestClient.Builder builder = mock(RestClient.Builder.class);
        RestClient restClient = mock(RestClient.class);
        when(builder.build()).thenReturn(restClient);
        KisRestRateLimiter rateLimiter = mock(KisRestRateLimiter.class);
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealOrderOrchestrator orders = mock(KisRealOrderOrchestrator.class);
        KisProperties properties = new KisProperties(false, false, false, false,
                new KisProperties.Account("", "", ""), new KisProperties.Account("", "", ""),
                Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofSeconds(1));

        KisRealExecutionNoticeClient client = new KisRealExecutionNoticeClient(false, "", properties,
                intents, sessions, orders, rateLimiter, builder, new ObjectMapper());
        try {
            client.connectWhenEnabled();
            verify(builder).build();
            verifyNoInteractions(restClient, rateLimiter, intents, orders, sessions);
        } finally {
            client.close();
        }
    }
}
