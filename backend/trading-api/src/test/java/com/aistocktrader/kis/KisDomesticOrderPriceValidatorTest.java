package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Duration;
import java.time.Instant;

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

    @Test
    void rejectsMissingAndStaleSymbolCatalogs(@TempDir Path tempDirectory) throws Exception {
        String previous = System.getProperty("kis.domestic.symbol-master-path");
        try {
            System.setProperty("kis.domestic.symbol-master-path", tempDirectory.resolve("missing.csv").toString());
            assertThrows(IllegalArgumentException.class,
                    () -> KisDomesticInstrumentCatalog.require("005930"));

            Path stale = tempDirectory.resolve("stale.csv");
            Files.writeString(stale, catalogContents(Instant.now()));
            Files.setLastModifiedTime(stale, FileTime.from(Instant.now().minus(Duration.ofDays(8))));
            System.setProperty("kis.domestic.symbol-master-path", stale.toString());
            assertThrows(IllegalArgumentException.class,
                    () -> KisDomesticInstrumentCatalog.require("005930"));
        } finally {
            restoreCatalogPath(previous);
        }
    }

    @Test
    void rejectsCatalogWithFutureGenerationTimestamp(@TempDir Path tempDirectory) throws Exception {
        String previous = System.getProperty("kis.domestic.symbol-master-path");
        try {
            Path future = tempDirectory.resolve("future.csv");
            Files.writeString(future, catalogContents(Instant.now().plus(Duration.ofHours(1))));
            System.setProperty("kis.domestic.symbol-master-path", future.toString());
            assertThrows(IllegalArgumentException.class,
                    () -> KisDomesticInstrumentCatalog.require("005930"));
        } finally {
            restoreCatalogPath(previous);
        }
    }

    private static String catalogContents(Instant generatedAt) {
        return "#generated_at=" + generatedAt + "\nsymbol,market,group_code\n005930,KOSPI,S\n";
    }

    private static void restoreCatalogPath(String previous) {
        if (previous == null) System.clearProperty("kis.domestic.symbol-master-path");
        else System.setProperty("kis.domestic.symbol-master-path", previous);
    }
}
