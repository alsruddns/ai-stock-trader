package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KisBrokerOrderSnapshotValidatorTest {
    private final KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.PAPER,
            new OrderRequest(Market.KR, "005930", OrderSide.BUY, 3, new BigDecimal("70000"), Currency.KRW), null);

    @Test
    void acceptsConsistentOpenAndTerminalSnapshots() {
        assertTrue(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(1, 2, new BigDecimal("70000"), false, false)));
        assertTrue(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(2, 0, new BigDecimal("70000"), true, false)));
        assertTrue(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(0, 0, null, false, true)));
    }

    @Test
    void rejectsMissingQuantityAndContradictoryTerminalFlags() {
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(1, 1, new BigDecimal("70000"), false, false)));
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(0, 1, null, true, false)));
        assertFalse(KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent,
                new KisBrokerOrderSnapshot(0, 0, null, true, true)));
    }
}
