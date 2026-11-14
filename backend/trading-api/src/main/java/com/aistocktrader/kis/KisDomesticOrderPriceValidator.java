package com.aistocktrader.kis;

import com.aistocktrader.order.Market;

import java.math.BigDecimal;

/** Validates price precision common to all domestic KRX-listed stock products. */
final class KisDomesticOrderPriceValidator {
    private KisDomesticOrderPriceValidator() { }

    static void validate(Market market, BigDecimal price) {
        if (market == Market.KR && price != null && price.stripTrailingZeros().scale() > 0) {
            throw new IllegalArgumentException("Domestic KIS order prices must be whole KRW amounts.");
        }
    }
}
