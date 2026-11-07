package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

class KisMarketAiEvidenceClientTest {
    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void fetchesActiveModelAndKisQuoteFromMarketServiceAndConfirmsEvidence() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/ai/models"))
                .andRespond(withSuccess("""
                        {"models":[{"model_id":"lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "market":"KR","active":true,"approved":true,
                        "feature_compatible":true,"data_rights_verified":true,
                        "approval_evidence_verified":true,"training_config":{"symbol":"005930"}}]}
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/quotes/KR/005930"))
                .andRespond(withSuccess("{\"market\":\"KR\",\"symbol\":\"005930\","
                        + "\"source\":\"KIS_REST_SNAPSHOT\",\"price\":70000,\"retrieved_at\":\""
                        + Instant.now() + "\"}", MediaType.APPLICATION_JSON));
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/paper/session"))
                .andRespond(withSuccess("{\"decisions\":{\"KR:005930:\":{\"market\":\"KR\","
                        + "\"symbol\":\"005930\",\"signal\":\"BUY_CANDIDATE\","
                        + "\"model_id\":\"lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"strategy_id\":\"ORB_VWAP\",\"evaluated_at\":\""
                        + Instant.now() + "\",\"stop_distance_per_share\":\"150\",\"executable\":false}}}",
                        MediaType.APPLICATION_JSON));

        var evidence = client.inspect(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), null);

        assertTrue(evidence.approvedModelCandidate());
        assertTrue(evidence.quoteFresh());
        assertEquals(new BigDecimal("150"), evidence.stopDistancePerShare());
        assertEquals("lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", evidence.modelId());
        assertEquals("ORB_VWAP", evidence.strategyId());
        assertEquals(new BigDecimal("70000"), evidence.quotePrice());
        server.verify();
    }

    @Test
    void exitQuoteFetchRequiresFreshMatchingKrQuoteAndReturnsBid() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/quotes/KR/005930"))
                .andRespond(withSuccess("{\"market\":\"KR\",\"symbol\":\"005930\","
                        + "\"source\":\"KIS_REST_SNAPSHOT\",\"price\":102,\"best_bid\":101.5,"
                        + "\"best_ask\":102,\"retrieved_at\":\"" + Instant.now() + "\"}",
                        MediaType.APPLICATION_JSON));

        var quote = client.quoteForExit(Market.KR, "005930", null);

        assertEquals(Market.KR, quote.market());
        assertEquals("005930", quote.symbol());
        assertEquals(new BigDecimal("102"), quote.price());
        assertEquals(new BigDecimal("101.5"), quote.bestBid());
        server.verify();
    }

    @Test
    void exitQuoteUsesRequestedUsVenueAndRejectsDifferentVenue() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/quotes/US/AAPL?exchange=NAS"))
                .andRespond(withSuccess("{\"market\":\"US\",\"symbol\":\"AAPL\",\"exchange\":\"NYS\","
                        + "\"source\":\"KIS_REST_SNAPSHOT\",\"price\":102,\"retrieved_at\":\""
                        + Instant.now() + "\"}", MediaType.APPLICATION_JSON));

        assertThrows(IllegalStateException.class, () -> client.quoteForExit(Market.US, "AAPL", "NASD"));
        server.verify();
    }

    @Test
    void exitQuoteRejectsStaleQuoteAndInvalidUsExchange() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/market/quotes/KR/005930"))
                .andRespond(withSuccess("{\"market\":\"KR\",\"symbol\":\"005930\","
                        + "\"source\":\"KIS_REST_SNAPSHOT\",\"price\":102,\"retrieved_at\":\""
                        + Instant.now().minusSeconds(10) + "\"}", MediaType.APPLICATION_JSON));

        assertThrows(IllegalStateException.class, () -> client.quoteForExit(Market.KR, "005930", null));
        assertThrows(IllegalArgumentException.class, () -> client.quoteForExit(Market.US, "AAPL", "BAD"));
        server.verify();
    }

    @Test
    void unavailableModelApiFailsClosedWithoutQuoteRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:18000");
        server.expect(requestTo("http://127.0.0.1:18000/api/v1/ai/models"))
                .andRespond(org.springframework.test.web.client.response.MockRestResponseCreators.withServerError());

        var evidence = client.inspect(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), null);

        assertFalse(evidence.approvedModelCandidate());
        assertFalse(evidence.quoteFresh());
        assertNull(evidence.stopDistancePerShare());
        server.verify();
    }

    @Test
    void acceptsOnlyIntegrityCheckedActiveApprovedModelForMarketAndSymbol() {
        ObjectNode response = mapper.valueToTree(Map.of("models", List.of(Map.of(
                "model_id", "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "market", "KR", "active", true, "approved", true,
                "feature_compatible", true, "data_rights_verified", true,
                "approval_evidence_verified", true,
                "training_config", Map.of("symbol", "005930")))));
        assertTrue(KisMarketAiEvidenceClient.hasActiveApprovedModel(response, "KR", "005930"));
        assertFalse(KisMarketAiEvidenceClient.hasActiveApprovedModel(response, "US", "005930"));
        assertFalse(KisMarketAiEvidenceClient.hasActiveApprovedModel(response, "KR", "000660"));
        ((ObjectNode) response.path("models").get(0)).put("active", false);
        assertFalse(KisMarketAiEvidenceClient.hasActiveApprovedModel(response, "KR", "005930"));
    }

    @Test
    void requiresRecentKisQuoteMatchingRequestedMarketAndSymbol() {
        Instant now = Instant.now();
        var request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        ObjectNode quote = mapper.valueToTree(Map.of("market", "KR", "symbol", "005930", "source", "KIS_REST_SNAPSHOT",
                "price", 70000, "retrieved_at", now.toString()));
        assertTrue(KisMarketAiEvidenceClient.isFreshMatchingQuote(quote, request));
        quote.put("source", "MOCK");
        assertFalse(KisMarketAiEvidenceClient.isFreshMatchingQuote(quote, request));
        quote.put("source", "KIS_REST_SNAPSHOT");
        quote.put("retrieved_at", now.minusSeconds(5).toString());
        assertFalse(KisMarketAiEvidenceClient.isFreshMatchingQuote(quote, request));
        quote.put("retrieved_at", now.plusSeconds(1).toString());
        assertFalse(KisMarketAiEvidenceClient.isFreshMatchingQuote(quote, request));
    }

    @Test
    void requiresFreshNonExecutableSignalFromTheApprovedModelAndPositiveStopDistance() {
        var request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        String modelId = "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        ObjectNode response = mapper.valueToTree(Map.of("decisions", Map.of("KR:005930:", Map.of(
                "market", "KR", "symbol", "005930", "signal", "BUY_CANDIDATE",
                "model_id", modelId, "strategy_id", "ORB_VWAP", "evaluated_at", Instant.now().toString(),
                "stop_distance_per_share", "105", "executable", false))));
        assertEquals(new BigDecimal("105"), KisMarketAiEvidenceClient.signalStopDistance(response, modelId, request, null));
        assertEquals("ORB_VWAP", KisMarketAiEvidenceClient.signalStrategyId(response, modelId, request, null));
        assertNull(KisMarketAiEvidenceClient.signalStopDistance(response, "other-model", request, null));
        assertNull(KisMarketAiEvidenceClient.signalStrategyId(response, "other-model", request, null));
        ((ObjectNode) response.path("decisions").path("KR:005930:")).put("strategy_id", "../untrusted");
        assertNull(KisMarketAiEvidenceClient.signalStrategyId(response, modelId, request, null));
        ((ObjectNode) response.path("decisions").path("KR:005930:")).put("signal", "HOLD");
        assertNull(KisMarketAiEvidenceClient.signalStopDistance(response, modelId, request, null));
    }
}
