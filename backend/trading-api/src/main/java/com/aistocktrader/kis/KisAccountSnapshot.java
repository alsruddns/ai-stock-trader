package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

public record KisAccountSnapshot(KisEnvironment environment, Market market, String currency,
                                 BigDecimal cashBalance, BigDecimal orderableCash,
                                 BigDecimal accountEquity,
                                 List<Position> positions, Instant retrievedAt,
                                 String source) {
    public KisAccountSnapshot(KisEnvironment environment, Market market, String currency,
                              BigDecimal cashBalance, BigDecimal orderableCash,
                              List<Position> positions, Instant retrievedAt, String source) {
        this(environment, market, currency, cashBalance, orderableCash, null, positions, retrievedAt, source);
    }

    public record Position(String symbol, String name, BigDecimal quantity,
                           BigDecimal sellableQuantity, BigDecimal averagePrice,
                           BigDecimal currentPrice, BigDecimal evaluationAmount,
                           BigDecimal profitLoss, BigDecimal profitLossPercent) {}
}
