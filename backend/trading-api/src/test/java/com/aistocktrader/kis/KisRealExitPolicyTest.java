package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class KisRealExitPolicyTest {
    private static final Instant NOW = Instant.parse("2026-11-07T12:00:00Z");
    private final KisRealExitPolicy policy = new KisRealExitPolicy(Duration.ofSeconds(5));

    @Test
    void holdsInsideValidatedExitBand() {
        assertEquals(KisRealExitPolicy.Decision.HOLD, policy.evaluate(input("95", "110", "102")));
    }

    @Test
    void triggersStopAtOrBelowStopPrice() {
        assertEquals(KisRealExitPolicy.Decision.STOP_LOSS, policy.evaluate(input("95", "110", "95")));
    }

    @Test
    void triggersTargetAtOrAboveTargetPrice() {
        assertEquals(KisRealExitPolicy.Decision.TAKE_PROFIT, policy.evaluate(input("95", "110", "111")));
    }

    @Test
    void sessionEndClosesBeforePriceEvaluation() {
        KisRealExitPolicy.Input input = new KisRealExitPolicy.Input(2, bd("100"), bd("95"), bd("110"),
                bd("120"), NOW, NOW, NOW);
        assertEquals(KisRealExitPolicy.Decision.SESSION_END, policy.evaluate(input));
    }

    @Test
    void staleMissingFutureOrNonPositiveQuoteBlocksExitDecision() {
        assertEquals(KisRealExitPolicy.Decision.BLOCKED_STALE_QUOTE,
                policy.evaluate(new KisRealExitPolicy.Input(2, bd("100"), bd("95"), bd("110"),
                        bd("102"), NOW.minusSeconds(6), NOW, NOW.plusSeconds(60))));
        assertEquals(KisRealExitPolicy.Decision.BLOCKED_STALE_QUOTE,
                policy.evaluate(new KisRealExitPolicy.Input(2, bd("100"), bd("95"), bd("110"),
                        bd("102"), NOW.plusSeconds(1), NOW, NOW.plusSeconds(60))));
        assertEquals(KisRealExitPolicy.Decision.BLOCKED_STALE_QUOTE,
                policy.evaluate(new KisRealExitPolicy.Input(2, bd("100"), bd("95"), bd("110"),
                        null, NOW, NOW, NOW.plusSeconds(60))));
    }

    @Test
    void malformedExitPlanIsRejected() {
        assertEquals(KisRealExitPolicy.Decision.INVALID_PLAN,
                policy.evaluate(new KisRealExitPolicy.Input(2, bd("100"), bd("100"), bd("110"),
                        bd("102"), NOW, NOW, NOW.plusSeconds(60))));
        assertEquals(KisRealExitPolicy.Decision.INVALID_PLAN,
                policy.evaluate(new KisRealExitPolicy.Input(0, bd("100"), bd("95"), bd("110"),
                        bd("102"), NOW, NOW, NOW.plusSeconds(60))));
    }

    @Test
    void requiresPositiveQuoteAgeLimit() {
        assertThrows(IllegalArgumentException.class, () -> new KisRealExitPolicy(Duration.ZERO));
    }

    private static KisRealExitPolicy.Input input(String stop, String target, String quote) {
        return new KisRealExitPolicy.Input(2, bd("100"), bd(stop), bd(target), bd(quote), NOW, NOW,
                NOW.plusSeconds(60));
    }

    private static BigDecimal bd(String value) {
        return value == null ? null : new BigDecimal(value);
    }
}
