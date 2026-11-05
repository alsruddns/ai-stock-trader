package com.aistocktrader.kis;

import java.time.Instant;
import java.util.UUID;

public record KisOrderIntentResponse(UUID idempotencyKey, String environment, String market,
                                    String symbol, String side, int quantity, String limitPrice,
                                    String currency, KisOrderStatus status, String brokerOrderNumber,
                                    String brokerOrganizationNumber, String responseCode,
                                    Integer filledQuantity, Integer remainingQuantity, String averageFillPrice,
                                    Instant createdAt, Instant updatedAt) {
    public static KisOrderIntentResponse from(KisOrderIntent intent) {
        return new KisOrderIntentResponse(intent.getIdempotencyKey(), intent.getEnvironment().label(), intent.getMarket().name(),
                intent.getSymbol(), intent.getSide().name(), intent.getQuantity(), intent.getLimitPrice().toPlainString(),
                intent.getCurrency().name(), intent.getStatus(), intent.getBrokerOrderNumber(),
                intent.getBrokerOrganizationNumber(), intent.getBrokerResponseCode(), intent.getFilledQuantity(),
                intent.getRemainingQuantity(), intent.getAverageFillPrice() == null ? null : intent.getAverageFillPrice().toPlainString(),
                intent.getCreatedAt(), intent.getUpdatedAt());
    }
}
