package com.aistocktrader.order;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PaperAutomationSafetyTest {
    @Test
    void blocksPaperAutomationWhenTransactionCostsAreDisabled() {
        PaperCostProperties costs = costs(false);

        assertThrows(IllegalStateException.class, () -> new PaperAutomationSafety(true, costs));
    }

    @Test
    void allowsPaperAutomationWhenTransactionCostsAreEnabled() {
        PaperCostProperties costs = costs(true);

        assertDoesNotThrow(() -> new PaperAutomationSafety(true, costs));
    }

    @Test
    void blocksPaperAutomationWhenDomesticCommissionIsZero() {
        PaperCostProperties costs = new PaperCostProperties(true, BigDecimal.ZERO, new BigDecimal("20"),
                new BigDecimal("25"), new BigDecimal("0.206"));

        assertThrows(IllegalStateException.class, () -> new PaperAutomationSafety(true, costs));
    }

    @Test
    void blocksPaperAutomationWhenDomesticSellCostIsZero() {
        PaperCostProperties costs = new PaperCostProperties(true, new BigDecimal("14.7"), BigDecimal.ZERO,
                new BigDecimal("25"), new BigDecimal("0.206"));

        assertThrows(IllegalStateException.class, () -> new PaperAutomationSafety(true, costs));
    }

    @Test
    void keepsManualPaperModeAvailableWhenAutomationIsDisabled() {
        PaperCostProperties costs = costs(false);

        assertDoesNotThrow(() -> new PaperAutomationSafety(false, costs));
    }

    private static PaperCostProperties costs(boolean enabled) {
        return new PaperCostProperties(enabled, new BigDecimal("14.7"), new BigDecimal("20"),
                new BigDecimal("25"), new BigDecimal("0.206"));
    }
}
