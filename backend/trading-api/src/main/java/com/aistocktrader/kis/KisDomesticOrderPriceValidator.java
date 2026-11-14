package com.aistocktrader.kis;

import com.aistocktrader.order.Market;

import java.math.BigDecimal;

/** Validates domestic KRX order prices using current KIS instrument classification. */
final class KisDomesticOrderPriceValidator {
    private KisDomesticOrderPriceValidator() { }

    static void validate(Market market, String symbol, BigDecimal price) {
        if (market != Market.KR || price == null) return;
        if (price.stripTrailingZeros().scale() > 0) {
            throw new IllegalArgumentException("Domestic KIS order prices must be whole KRW amounts.");
        }
        if (symbol == null || !symbol.matches("\\d{6}")) {
            throw new IllegalArgumentException("Domestic KIS orders require a six-digit symbol for tick validation.");
        }
        KisDomesticInstrumentCatalog.Instrument instrument = KisDomesticInstrumentCatalog.require(symbol);
        if (!"S".equals(instrument.groupCode())) {
            throw new IllegalArgumentException("This domestic KIS product group is not enabled for order submission.");
        }
        int tick = tickSize(price);
        if (price.remainder(BigDecimal.valueOf(tick)).signum() != 0) {
            throw new IllegalArgumentException("Domestic KIS order price does not match the current KRX stock tick size.");
        }
    }

    private static int tickSize(BigDecimal price) {
        if (price.compareTo(new BigDecimal("2000")) < 0) return 1;
        if (price.compareTo(new BigDecimal("5000")) < 0) return 5;
        if (price.compareTo(new BigDecimal("20000")) < 0) return 10;
        if (price.compareTo(new BigDecimal("50000")) < 0) return 50;
        if (price.compareTo(new BigDecimal("200000")) < 0) return 100;
        if (price.compareTo(new BigDecimal("500000")) < 0) return 500;
        return 1000;
    }
}
