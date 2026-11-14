package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;

@RestController
@RequestMapping("/api/v1/kis/accounts/realized-pnl/real/KR")
public class KisDomesticCostReconciliationController {
    private final KisDomesticCostReconciliationService reconciliation;
    private final MarketOperationalScope marketScope;

    public KisDomesticCostReconciliationController(KisDomesticCostReconciliationService reconciliation,
                                                    MarketOperationalScope marketScope) {
        this.reconciliation = reconciliation;
        this.marketScope = marketScope;
    }

    @GetMapping("/{sessionDate}/cost-reconciliation")
    public KisDomesticCostReconciliationService.Reconciliation reconcile(@PathVariable LocalDate sessionDate) {
        marketScope.require(Market.KR, "KIS domestic cost reconciliation");
        return reconciliation.reconcile(sessionDate);
    }
}
