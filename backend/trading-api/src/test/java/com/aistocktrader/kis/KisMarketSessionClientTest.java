package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.time.Instant;
import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

class KisMarketSessionClientTest {
    @Test
    void readsVerifiedSessionWindowAndDoesNotChangeSharedRestClientBuilder() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisMarketSessionClient client = new KisMarketSessionClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/sessions/KR"))
                .andRespond(withSuccess("""
                        {"market":"KR","session_date":"2026-11-09","is_session":true,
                         "is_open":true,"open_at":"2026-11-09T00:00:00Z",
                         "close_at":"2026-11-09T06:30:00Z",
                         "continuous_open_at":"2026-11-09T00:00:00Z",
                         "continuous_close_at":"2026-11-09T06:20:00Z",
                         "evaluated_at":"2026-11-09T00:01:00Z"}
                        """, MediaType.APPLICATION_JSON));

        KisMarketSessionClient.MarketSession session = client.session(Market.KR);

        assertEquals(Market.KR, session.market());
        assertEquals(LocalDate.parse("2026-11-09"), session.sessionDate());
        assertTrue(session.session());
        assertTrue(session.open());
        assertEquals(Instant.parse("2026-11-09T00:00:00Z"), session.openAt());
        assertEquals(Instant.parse("2026-11-09T06:20:00Z"), session.continuousCloseAt());
        server.verify();
    }

    @Test
    void readsLatestClosedExchangeSessionForCrossMarketRiskEvidence() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisMarketSessionClient client = new KisMarketSessionClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/sessions/US/latest-closed"))
                .andRespond(withSuccess("""
                        {"market":"US","session_date":"2026-11-06","is_session":true,
                         "is_open":false,"open_at":"2026-11-06T13:30:00Z",
                         "close_at":"2026-11-06T20:00:00Z",
                         "continuous_open_at":"2026-11-06T13:30:00Z",
                         "continuous_close_at":"2026-11-06T20:00:00Z",
                         "evaluated_at":"2026-11-09T00:10:00Z"}
                        """, MediaType.APPLICATION_JSON));

        KisMarketSessionClient.MarketSession session = client.latestClosedSession(Market.US);

        assertEquals(Market.US, session.market());
        assertEquals(LocalDate.parse("2026-11-06"), session.sessionDate());
        assertTrue(session.session());
        assertEquals(false, session.open());
        assertEquals(Instant.parse("2026-11-06T13:30:00Z"), session.openAt());
        assertEquals(Instant.parse("2026-11-06T20:00:00Z"), session.closeAt());
        server.verify();
    }

    @Test
    void malformedSessionBoundsAreRejected() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisMarketSessionClient client = new KisMarketSessionClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/sessions/US"))
                .andRespond(withSuccess("""
                        {"market":"US","session_date":"2026-11-06","is_session":true,
                         "is_open":false,"open_at":null,"close_at":null,
                         "evaluated_at":"2026-11-06T14:00:00Z"}
                        """, MediaType.APPLICATION_JSON));

        assertThrows(IllegalStateException.class, () -> client.session(Market.US));
        server.verify();
    }
}
