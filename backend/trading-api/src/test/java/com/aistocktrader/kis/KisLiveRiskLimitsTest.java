package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class KisLiveRiskLimitsTest {
    @Test
    void permitsApprovedLimitsAndSaferOverrides() {
        assertDoesNotThrow(() -> limits("1.00", "0.25", "10.00", 3, 3));
        assertDoesNotThrow(() -> limits("0.50", "0.10", "5.00", 1, 1));
    }

    @Test
    void rejectsOverridesThatLoosenAnyApprovedLiveLimit() {
        assertThrows(IllegalStateException.class, () -> limits("1.01", "0.25", "10.00", 3, 3));
        assertThrows(IllegalStateException.class, () -> limits("1.00", "0.26", "10.00", 3, 3));
        assertThrows(IllegalStateException.class, () -> limits("1.00", "0.25", "10.01", 3, 3));
        assertThrows(IllegalStateException.class, () -> limits("1.00", "0.25", "10.00", 4, 3));
        assertThrows(IllegalStateException.class, () -> limits("1.00", "0.25", "10.00", 3, 4));
    }

    private static KisLiveRiskLimits limits(String dailyLoss, String tradeRisk, String position,
                                             int openPositions, int consecutiveLosses) {
        return new KisLiveRiskLimits(new BigDecimal(dailyLoss), new BigDecimal(tradeRisk),
                new BigDecimal(position), openPositions, consecutiveLosses);
    }
}
