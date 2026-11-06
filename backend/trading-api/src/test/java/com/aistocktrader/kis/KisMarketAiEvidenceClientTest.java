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
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:8000");
        server.expect(requestTo("http://127.0.0.1:8000/api/v1/ai/models"))
                .andRespond(withSuccess("""
                        {"models":[{"market":"KR","active":true,"approved":true,
                        "feature_compatible":true,"data_rights_verified":true,
                        "approval_evidence_verified":true,"training_config":{"symbol":"005930"}}]}
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo("http://127.0.0.1:8000/api/v1/market/quotes/KR/005930"))
                .andRespond(withSuccess("{\"market\":\"KR\",\"symbol\":\"005930\","
                        + "\"source\":\"KIS_REST_SNAPSHOT\",\"price\":70000,\"retrieved_at\":\""
                        + Instant.now() + "\"}", MediaType.APPLICATION_JSON));

        var evidence = client.inspect(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), null);

        assertTrue(evidence.approvedModelCandidate());
        assertTrue(evidence.quoteFresh());
        server.verify();
    }

    @Test
    void unavailableModelApiFailsClosedWithoutQuoteRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var client = new KisMarketAiEvidenceClient(builder, "http://127.0.0.1:8000");
        server.expect(requestTo("http://127.0.0.1:8000/api/v1/ai/models"))
                .andRespond(org.springframework.test.web.client.response.MockRestResponseCreators.withServerError());

        var evidence = client.inspect(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), null);

        assertFalse(evidence.approvedModelCandidate());
        assertFalse(evidence.quoteFresh());
        server.verify();
    }

    @Test
    void acceptsOnlyIntegrityCheckedActiveApprovedModelForMarketAndSymbol() {
        ObjectNode response = mapper.valueToTree(Map.of("models", List.of(Map.of(
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
}
