package com.aistocktrader.order;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
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

    @Test
    void springBlocksAutomationWhenConfiguredCostsAreDisabled() {
        new ApplicationContextRunner()
                .withUserConfiguration(PaperAutomationSafety.class)
                .withBean(PaperCostProperties.class, () -> new PaperCostProperties(true, BigDecimal.ZERO,
                        BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO))
                .withPropertyValues("paper.automation-enabled=true")
                .run(context -> {
                    Throwable failure = context.getStartupFailure();
                    assertNotNull(failure);
                    assertEquals("Domestic PAPER automation requires positive commission and sell-cost assumptions.",
                            rootCause(failure).getMessage());
                });
    }

    @Test
    void springAllowsAutomationWhenConfiguredCostsAreValid() {
        new ApplicationContextRunner()
                .withUserConfiguration(PaperAutomationSafety.class)
                .withBean(PaperCostProperties.class, () -> costs(true))
                .withPropertyValues("paper.automation-enabled=true")
                .run(context -> assertNull(context.getStartupFailure()));
    }

    private static Throwable rootCause(Throwable exception) {
        Throwable current = exception;
        while (current.getCause() != null) current = current.getCause();
        return current;
    }

    private static PaperCostProperties costs(boolean enabled) {
        return new PaperCostProperties(enabled, new BigDecimal("14.7"), new BigDecimal("20"),
                new BigDecimal("25"), new BigDecimal("0.206"));
    }
}
