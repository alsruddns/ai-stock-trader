package com.aistocktrader.kis;

import com.aistocktrader.order.OrderRequest;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.time.Instant;

/** Reads model approval and quote freshness from the market service; caller booleans are not trusted. */
@Component
public class KisMarketAiEvidenceClient {
    private static final Duration MAX_QUOTE_AGE = Duration.ofSeconds(3);
    private final RestClient http;

    public KisMarketAiEvidenceClient(RestClient.Builder builder,
            @Value("${market-ai.base-url:http://127.0.0.1:8000}") String baseUrl) {
        this.http = builder.clone().baseUrl(baseUrl).build();
    }

    public Evidence inspect(OrderRequest request, String exchange) {
        if (request == null || request.market() == null || request.symbol() == null || request.symbol().isBlank()) {
            return new Evidence(false, false);
        }
        try {
            JsonNode models = http.get().uri("/api/v1/ai/models").retrieve().body(JsonNode.class);
            boolean approved = hasActiveApprovedModel(models, request.market().name(), request.symbol());
            JsonNode quote = request.market() == com.aistocktrader.order.Market.US
                    ? http.get().uri(uri -> uri.path("/api/v1/market/quotes/US/{symbol}")
                            .queryParam("exchange", marketAiExchange(exchange)).build(request.symbol()))
                            .retrieve().body(JsonNode.class)
                    : http.get().uri("/api/v1/market/quotes/KR/{symbol}", request.symbol())
                            .retrieve().body(JsonNode.class);
            return new Evidence(approved, isFreshMatchingQuote(quote, request));
        } catch (RuntimeException unavailable) {
            return new Evidence(false, false);
        }
    }

    static boolean hasActiveApprovedModel(JsonNode response, String market, String symbol) {
        JsonNode items = response == null ? null : response.path("models");
        if (items == null || !items.isArray()) return false;
        for (JsonNode item : items) {
            if (item.path("active").asBoolean(false) && item.path("approved").asBoolean(false)
                    && item.path("feature_compatible").asBoolean(false)
                    && item.path("data_rights_verified").asBoolean(false)
                    && item.path("approval_evidence_verified").asBoolean(false)
                    && market.equals(item.path("market").asText())
                    && symbol.equalsIgnoreCase(item.path("training_config").path("symbol").asText())) return true;
        }
        return false;
    }

    static boolean isFreshMatchingQuote(JsonNode quote, OrderRequest request) {
        if (quote == null || !request.market().name().equals(quote.path("market").asText())
                || !request.symbol().equalsIgnoreCase(quote.path("symbol").asText())
                || !"KIS_REST_SNAPSHOT".equalsIgnoreCase(quote.path("source").asText())
                || !quote.path("price").isNumber() || quote.path("price").decimalValue().signum() <= 0
                || !quote.path("retrieved_at").isTextual()) return false;
        try {
            Instant retrieved = Instant.parse(quote.path("retrieved_at").asText());
            Instant now = Instant.now();
            return !retrieved.isAfter(now) && Duration.between(retrieved, now).compareTo(MAX_QUOTE_AGE) <= 0;
        } catch (RuntimeException invalidTimestamp) {
            return false;
        }
    }

    private static String marketAiExchange(String exchange) {
        if (exchange == null) return "";
        return switch (exchange.trim().toUpperCase(java.util.Locale.ROOT)) {
            case "NASD", "NAS" -> "NAS";
            case "NYSE", "NYS" -> "NYS";
            case "AMEX", "AMS" -> "AMS";
            default -> "";
        };
    }

    public record Evidence(boolean approvedModelCandidate, boolean quoteFresh) { }
}
