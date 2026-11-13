package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.UUID;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;

class KisRealFillSnapshotStoreTest {
    @Test
    void overflowingCumulativeQuantityIsNotPersisted() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        KisRealFillSnapshotStore store = new KisRealFillSnapshotStore(jdbc);
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, Integer.MAX_VALUE,
                        new BigDecimal("70000"), Currency.KRW), null);

        store.record(intent, new KisBrokerOrderSnapshot(1_500_000_000, 1_000_000_000,
                new BigDecimal("70000"), false, false));

        verifyNoInteractions(jdbc);
    }
}
