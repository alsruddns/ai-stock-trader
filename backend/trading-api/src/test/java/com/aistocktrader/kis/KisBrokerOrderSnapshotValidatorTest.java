package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KisBrokerOrderSnapshotValidatorTest {
    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void domesticBrokerIdentityRequiresMatchingOrganizationWhenKnown() throws Exception {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        assertTrue(matches(intent, "{\"pdno\":\"005930\",\"sll_buy_dvsn_cd\":\"02\",\"ord_gno_brno\":\"ORG01\"}"));
        assertFalse(matches(intent, "{\"pdno\":\"000660\",\"sll_buy_dvsn_cd\":\"02\",\"ord_gno_brno\":\"ORG01\"}"));
        assertFalse(matches(intent, "{\"pdno\":\"005930\",\"sll_buy_dvsn_cd\":\"01\",\"ord_gno_brno\":\"ORG01\"}"));
        assertFalse(matches(intent, "{\"pdno\":\"005930\",\"sll_buy_dvsn_cd\":\"02\",\"ord_gno_brno\":\"ORG02\"}"));
        assertFalse(matches(intent, "{\"pdno\":\"005930\",\"sll_buy_dvsn_cd\":\"02\"}"));
        assertFalse(matches(intent, "{\"pdno\":\"005930\",\"ord_gno_brno\":\"ORG01\"}"));
    }

    @Test
    void overseasIdentityAllowsUnavailableBrokerOrganizationField() throws Exception {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new OrderRequest(Market.US, "AAPL", OrderSide.SELL, 1,
                        new BigDecimal("100"), Currency.USD), "NASD");
        intent.accept(new KisOrderReceipt("real", "US", "AAPL", "SELL", "5678", "ORG02", "0", Instant.now()));

        assertTrue(matches(intent, "{\"pdno\":\"AAPL\",\"sll_buy_dvsn\":\"01\"}"));
    }

    @Test
    void filledBrokerSnapshotRequiresPositiveAverageFillPrice() {
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW), null);

        assertTrue(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(0, 2, null, false, false)));
        assertTrue(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(1, 1, new BigDecimal("69900"), false, false)));
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(1, 1, null, false, false)));
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(1, 1, BigDecimal.ZERO, false, false)));
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(1, 1, new BigDecimal("-1"), false, false)));
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(0, 2, new BigDecimal("-1"), false, false)));
    }

    private boolean matches(KisOrderIntent intent, String json) throws Exception {
        return KisBrokerOrderSnapshotValidator.matchesBrokerIdentity(mapper.readTree(json), intent);
    }
}
