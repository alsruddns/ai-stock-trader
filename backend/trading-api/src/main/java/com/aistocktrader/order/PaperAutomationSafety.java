package com.aistocktrader.order;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** Prevents automatic PAPER operation from recording zero-cost performance. */
@Component
public class PaperAutomationSafety {
    public PaperAutomationSafety(@Value("${paper.automation-enabled:false}") boolean automationEnabled,
                                 PaperCostProperties costs) {
        if (automationEnabled && !costs.enabled()) {
            throw new IllegalStateException("PAPER automation requires the transaction cost model to be enabled.");
        }
    }
}
