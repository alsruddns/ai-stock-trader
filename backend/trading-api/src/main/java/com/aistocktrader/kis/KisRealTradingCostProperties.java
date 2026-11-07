package com.aistocktrader.kis;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;

/** Account-specific execution costs; unset or malformed rates keep the REAL buy risk gate closed. */
@Component
public class KisRealTradingCostProperties {
    private final String krBuyCommission;
    private final String krSellCommission;
    private final String krSellTax;
    private final String usBuyCommission;
    private final String usSellCommission;
    private final String usSellTax;

    public KisRealTradingCostProperties(
            @Value("${kis.real-costs.kr-buy-commission-bps:}") String krBuyCommission,
            @Value("${kis.real-costs.kr-sell-commission-bps:}") String krSellCommission,
            @Value("${kis.real-costs.kr-sell-tax-bps:}") String krSellTax,
            @Value("${kis.real-costs.us-buy-commission-bps:}") String usBuyCommission,
            @Value("${kis.real-costs.us-sell-commission-bps:}") String usSellCommission,
            @Value("${kis.real-costs.us-sell-tax-bps:}") String usSellTax) {
        this.krBuyCommission = krBuyCommission;
        this.krSellCommission = krSellCommission;
        this.krSellTax = krSellTax;
        this.usBuyCommission = usBuyCommission;
        this.usSellCommission = usSellCommission;
        this.usSellTax = usSellTax;
    }

    KisRealizedPnlCalculator.Costs forMarket(com.aistocktrader.order.Market market) {
        if (market == null) return null;
        String buy = market == com.aistocktrader.order.Market.KR ? krBuyCommission : usBuyCommission;
        String sell = market == com.aistocktrader.order.Market.KR ? krSellCommission : usSellCommission;
        String tax = market == com.aistocktrader.order.Market.KR ? krSellTax : usSellTax;
        try {
            return new KisRealizedPnlCalculator.Costs(new BigDecimal(buy), new BigDecimal(sell), new BigDecimal(tax));
        } catch (RuntimeException invalid) {
            return null;
        }
    }

    public boolean configuredFor(com.aistocktrader.order.Market market) {
        KisRealizedPnlCalculator.Costs configured = forMarket(market);
        return configured != null && configured.valid();
    }
}
