package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class KisDomesticOrderPriceValidatorTest {
    @Test
    void acceptsOrdinarySharePricesAtKrxTickBoundaries() {
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("1999")));
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("2005")));
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("5010")));
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("20050")));
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("50100")));
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("200500")));
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("500000")));
    }

    @Test
    void rejectsPricesThatDoNotMatchTheCurrentKrxTick() {
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("5001")));
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("2001")));
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("50050")));
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("200100")));
    }

    @Test
    void rejectsUnknownAndUnsupportedProductsBeforeOrderSubmission() {
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "999999", new BigDecimal("1000")));
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "069500", new BigDecimal("35000")));
        assertThrows(IllegalArgumentException.class,
                () -> KisDomesticOrderPriceValidator.validate(Market.KR, "005930", new BigDecimal("70000.5")));
    }

    @Test
    void doesNotApplyDomesticTickRulesToUsPrices() {
        assertDoesNotThrow(() -> KisDomesticOrderPriceValidator.validate(Market.US, "AAPL", new BigDecimal("199.99")));
    }
}
