package com.aistocktrader.kis;

import java.time.Instant;
import java.util.UUID;

public record KisOrderIntentResponse(UUID idempotencyKey, String environment, String market,
                                    String symbol, String side, int quantity, String limitPrice,
                                    String currency, KisOrderStatus status, String brokerOrderNumber,
                                    String brokerOrganizationNumber, String responseCode,
                                    Instant createdAt, Instant updatedAt) {
    public static KisOrderIntentResponse from(KisOrderIntent intent) {
        return new KisOrderIntentResponse(intent.getIdempotencyKey(), "paper", intent.getMarket().name(),
                intent.getSymbol(), intent.getSide().name(), intent.getQuantity(), intent.getLimitPrice().toPlainString(),
                intent.getCurrency().name(), intent.getStatus(), intent.getBrokerOrderNumber(),
                intent.getBrokerOrganizationNumber(), intent.getBrokerResponseCode(), intent.getCreatedAt(), intent.getUpdatedAt());
    }
}
