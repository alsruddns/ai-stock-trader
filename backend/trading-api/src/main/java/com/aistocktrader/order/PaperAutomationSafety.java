package com.aistocktrader.order;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** Prevents automatic PAPER operation from recording zero-cost performance. */
@Component
public class PaperAutomationSafety {
    public PaperAutomationSafety(@Value("${paper.automation-enabled:false}") boolean automationEnabled,
                                 PaperCostProperties costs) {
        if (!automationEnabled) return;
        if (!costs.enabled()) {
            throw new IllegalStateException("PAPER automation requires the transaction cost model to be enabled.");
        }
        if (costs.krCommissionBps().signum() <= 0 || costs.krSellCostBps().signum() <= 0) {
            throw new IllegalStateException("Domestic PAPER automation requires positive commission and sell-cost assumptions.");
        }
    }
}
