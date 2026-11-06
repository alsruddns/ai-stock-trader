package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Instant;
import java.time.LocalDate;

/** Reads exchange session truth from the market-analysis service's exchange-calendar adapter. */
@Component
public class KisMarketSessionClient {
    private final RestClient http;

    public KisMarketSessionClient(RestClient.Builder builder,
                                  @Value("${market-ai.base-url:http://127.0.0.1:8000}") String baseUrl) {
        this.http = builder.clone().baseUrl(baseUrl).build();
    }

    public MarketSession session(Market market) {
        JsonNode payload = http.get().uri("/api/v1/market/sessions/{market}", market.name())
                .retrieve().body(JsonNode.class);
        if (payload == null || !market.name().equals(payload.path("market").asText())
                || !payload.path("session_date").isTextual()
                || !payload.path("is_session").isBoolean()
                || !payload.path("is_open").isBoolean()
                || !payload.path("evaluated_at").isTextual()) {
            throw new IllegalStateException("Market session response is incomplete.");
        }
        LocalDate sessionDate = LocalDate.parse(payload.path("session_date").asText());
        Instant evaluatedAt = Instant.parse(payload.path("evaluated_at").asText());
        Instant openAt = payload.path("open_at").isTextual()
                ? Instant.parse(payload.path("open_at").asText()) : null;
        Instant closeAt = payload.path("close_at").isTextual()
                ? Instant.parse(payload.path("close_at").asText()) : null;
        boolean isSession = payload.path("is_session").asBoolean();
        if (isSession != (openAt != null && closeAt != null) || (isSession && !closeAt.isAfter(openAt))) {
            throw new IllegalStateException("Market session bounds are inconsistent.");
        }
        return new MarketSession(market, sessionDate, isSession,
                payload.path("is_open").asBoolean(), openAt, closeAt, evaluatedAt);
    }

    public record MarketSession(Market market, LocalDate sessionDate, boolean session,
                                boolean open, Instant openAt, Instant closeAt, Instant evaluatedAt) { }
}
