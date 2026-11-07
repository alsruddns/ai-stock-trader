package com.aistocktrader.kis;

import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.OrderSide;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.time.Instant;
import java.math.BigDecimal;

/** Reads model approval and quote freshness from the market service; caller booleans are not trusted. */
@Component
public class KisMarketAiEvidenceClient {
    private static final Duration MAX_QUOTE_AGE = Duration.ofSeconds(3);
    private static final Duration MAX_SIGNAL_AGE = Duration.ofSeconds(120);
    private final RestClient http;

    public KisMarketAiEvidenceClient(RestClient.Builder builder,
            @Value("${market-ai.base-url:http://127.0.0.1:18000}") String baseUrl) {
        this.http = builder.clone().baseUrl(baseUrl).build();
    }

    public Evidence inspect(OrderRequest request, String exchange) {
        if (request == null || request.market() == null || request.symbol() == null || request.symbol().isBlank()) {
            return new Evidence(false, false, null);
        }
        try {
            JsonNode models = http.get().uri("/api/v1/ai/models").retrieve().body(JsonNode.class);
            String modelId = activeApprovedModelId(models, request.market().name(), request.symbol());
            if (modelId == null) return new Evidence(false, false, null);
            JsonNode quote = request.market() == com.aistocktrader.order.Market.US
                    ? http.get().uri(uri -> uri.path("/api/v1/market/quotes/US/{symbol}")
                            .queryParam("exchange", marketAiExchange(exchange)).build(request.symbol()))
                            .retrieve().body(JsonNode.class)
                    : http.get().uri("/api/v1/market/quotes/KR/{symbol}", request.symbol())
                            .retrieve().body(JsonNode.class);
            JsonNode paper = http.get().uri("/api/v1/paper/session").retrieve().body(JsonNode.class);
            BigDecimal stopDistance = signalStopDistance(paper, modelId, request, exchange);
            String strategyId = signalStrategyId(paper, modelId, request, exchange);
            boolean approvedCandidate = stopDistance != null && strategyId != null;
            BigDecimal quotePrice = quote != null && quote.path("price").isNumber()
                    ? quote.path("price").decimalValue() : null;
            return new Evidence(approvedCandidate, isFreshMatchingQuote(quote, request), stopDistance,
                    approvedCandidate ? modelId : null, approvedCandidate ? strategyId : null, quotePrice);
        } catch (RuntimeException unavailable) {
            return new Evidence(false, false, null);
        }
    }

    /** Fetches a fresh KIS quote snapshot for a server-side protective-exit decision. */
    public ExitQuote quoteForExit(com.aistocktrader.order.Market market, String symbol, String exchange) {
        if (market == null || symbol == null || symbol.isBlank()) {
            throw new IllegalArgumentException("Exit quote market and symbol are required.");
        }
        String normalizedSymbol = symbol.trim().toUpperCase(java.util.Locale.ROOT);
        String normalizedExchange = market == com.aistocktrader.order.Market.US
                ? marketAiExchange(exchange) : null;
        if (market == com.aistocktrader.order.Market.KR
                ? !normalizedSymbol.matches("\\d{6}") || exchange != null
                : normalizedExchange == null || normalizedExchange.isBlank()) {
            throw new IllegalArgumentException("Exit quote market or exchange is invalid.");
        }
        JsonNode quote = market == com.aistocktrader.order.Market.US
                ? http.get().uri(uri -> uri.path("/api/v1/market/quotes/US/{symbol}")
                        .queryParam("exchange", normalizedExchange).build(normalizedSymbol))
                        .retrieve().body(JsonNode.class)
                : http.get().uri("/api/v1/market/quotes/KR/{symbol}", normalizedSymbol)
                        .retrieve().body(JsonNode.class);
        OrderRequest sellProbe = new OrderRequest(market, normalizedSymbol, OrderSide.SELL, 1,
                BigDecimal.ONE, market == com.aistocktrader.order.Market.KR ? Currency.KRW : Currency.USD);
        if (!isFreshMatchingQuote(quote, sellProbe)) {
            throw new IllegalStateException("Protective exit requires a fresh matching KIS quote.");
        }
        if (market == com.aistocktrader.order.Market.US
                && !normalizedExchange.equalsIgnoreCase(quote.path("exchange").asText())) {
            throw new IllegalStateException("Protective exit quote exchange does not match the position.");
        }
        return new ExitQuote(market, normalizedSymbol,
                market == com.aistocktrader.order.Market.US ? normalizedExchange : null,
                quote.path("price").decimalValue(), decimalOrNull(quote.path("best_bid")),
                decimalOrNull(quote.path("best_ask")), Instant.parse(quote.path("retrieved_at").asText()));
    }

    private static BigDecimal decimalOrNull(JsonNode value) {
        if (value == null || value.isNull() || value.isMissingNode() || !value.isNumber()) return null;
        BigDecimal decimal = value.decimalValue();
        return decimal.signum() > 0 ? decimal : null;
    }

    static boolean hasActiveApprovedModel(JsonNode response, String market, String symbol) {
        return activeApprovedModelId(response, market, symbol) != null;
    }

    static String activeApprovedModelId(JsonNode response, String market, String symbol) {
        JsonNode items = response == null ? null : response.path("models");
        if (items == null || !items.isArray()) return null;
        for (JsonNode item : items) {
            if (item.path("active").asBoolean(false) && item.path("approved").asBoolean(false)
                    && item.path("feature_compatible").asBoolean(false)
                    && item.path("data_rights_verified").asBoolean(false)
                    && item.path("approval_evidence_verified").asBoolean(false)
                    && market.equals(item.path("market").asText())
                    && symbol.equalsIgnoreCase(item.path("training_config").path("symbol").asText())
                    && item.path("model_id").isTextual()) return item.path("model_id").asText();
        }
        return null;
    }

    static BigDecimal signalStopDistance(JsonNode response, String modelId, OrderRequest request, String exchange) {
        JsonNode decision = signalDecision(response, modelId, request, exchange);
        if (decision == null || decision.isMissingNode()
                || !decision.path("stop_distance_per_share").isTextual()) return null;
        try {
            BigDecimal distance = new BigDecimal(decision.path("stop_distance_per_share").asText());
            return distance.signum() > 0 ? distance : null;
        } catch (RuntimeException invalidEvidence) {
            return null;
        }
    }

    static String signalStrategyId(JsonNode response, String modelId, OrderRequest request, String exchange) {
        JsonNode decision = signalDecision(response, modelId, request, exchange);
        if (decision == null || decision.isMissingNode()) return null;
        String strategyId = decision.path("strategy_id").asText(null);
        return strategyId != null && strategyId.matches("[A-Za-z0-9._-]{1,64}") ? strategyId : null;
    }

    private static JsonNode signalDecision(JsonNode response, String modelId, OrderRequest request, String exchange) {
        if (response == null || request == null || request.market() == null || request.symbol() == null
                || modelId == null) return null;
        String normalizedExchange = request.market() == com.aistocktrader.order.Market.US
                ? marketAiExchange(exchange) : "";
        String key = request.market().name() + ":" + request.symbol().toUpperCase(java.util.Locale.ROOT)
                + ":" + normalizedExchange;
        JsonNode decision = response.path("decisions").path(key);
        if (decision.isMissingNode()
                || !request.market().name().equals(decision.path("market").asText())
                || !request.symbol().equalsIgnoreCase(decision.path("symbol").asText())
                || !"BUY_CANDIDATE".equals(decision.path("signal").asText())
                || !modelId.equals(decision.path("model_id").asText())
                || decision.path("executable").asBoolean(true)
                || !decision.path("evaluated_at").isTextual()) return null;
        if (request.market() == com.aistocktrader.order.Market.US
                && !normalizedExchange.equals(decision.path("exchange").asText())) return null;
        try {
            Instant at = Instant.parse(decision.path("evaluated_at").asText());
            Instant now = Instant.now();
            if (at.isAfter(now.plusSeconds(5)) || Duration.between(at, now).compareTo(MAX_SIGNAL_AGE) > 0) return null;
            return decision;
        } catch (RuntimeException invalidEvidence) {
            return null;
        }
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

    public record Evidence(boolean approvedModelCandidate, boolean quoteFresh, BigDecimal stopDistancePerShare,
                           String modelId, String strategyId, BigDecimal quotePrice) {
        public Evidence(boolean approvedModelCandidate, boolean quoteFresh, BigDecimal stopDistancePerShare,
                        String modelId, String strategyId) {
            this(approvedModelCandidate, quoteFresh, stopDistancePerShare, modelId, strategyId, null);
        }

        public Evidence(boolean approvedModelCandidate, boolean quoteFresh, BigDecimal stopDistancePerShare) {
            this(approvedModelCandidate, quoteFresh, stopDistancePerShare, null, null, null);
        }
    }

    public record ExitQuote(com.aistocktrader.order.Market market, String symbol, String exchange,
                            BigDecimal price, BigDecimal bestBid, BigDecimal bestAsk, Instant retrievedAt) { }
}
