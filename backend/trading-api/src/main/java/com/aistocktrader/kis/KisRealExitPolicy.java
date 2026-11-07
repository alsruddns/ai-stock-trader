package com.aistocktrader.kis;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;

/** Pure REAL-position exit decision. It does not read accounts or submit broker orders. */
public final class KisRealExitPolicy {
    private final Duration maximumQuoteAge;

    public KisRealExitPolicy(Duration maximumQuoteAge) {
        if (maximumQuoteAge == null || maximumQuoteAge.isNegative() || maximumQuoteAge.isZero()) {
            throw new IllegalArgumentException("Maximum quote age must be positive.");
        }
        this.maximumQuoteAge = maximumQuoteAge;
    }

    public Decision evaluate(Input input) {
        if (input == null || input.quantity() <= 0 || !positive(input.averageFillPrice())
                || !positive(input.stopPrice()) || !positive(input.targetPrice())
                || input.stopPrice().compareTo(input.averageFillPrice()) >= 0
                || input.targetPrice().compareTo(input.averageFillPrice()) <= 0
                || input.now() == null || input.sessionEnd() == null) {
            return Decision.INVALID_PLAN;
        }
        if (input.quotePrice() == null || input.quoteObservedAt() == null
                || input.quoteObservedAt().isAfter(input.now())
                || Duration.between(input.quoteObservedAt(), input.now()).compareTo(maximumQuoteAge) > 0
                || input.quotePrice().signum() <= 0) {
            return Decision.BLOCKED_STALE_QUOTE;
        }
        if (!input.now().isBefore(input.sessionEnd())) return Decision.SESSION_END;
        if (input.quotePrice().compareTo(input.stopPrice()) <= 0) return Decision.STOP_LOSS;
        if (input.quotePrice().compareTo(input.targetPrice()) >= 0) return Decision.TAKE_PROFIT;
        return Decision.HOLD;
    }

    private static boolean positive(BigDecimal value) {
        return value != null && value.signum() > 0;
    }

    public enum Decision {
        HOLD,
        STOP_LOSS,
        TAKE_PROFIT,
        SESSION_END,
        BLOCKED_STALE_QUOTE,
        INVALID_PLAN
    }

    public record Input(int quantity, BigDecimal averageFillPrice, BigDecimal stopPrice,
                        BigDecimal targetPrice, BigDecimal quotePrice, Instant quoteObservedAt,
                        Instant now, Instant sessionEnd) { }
}
