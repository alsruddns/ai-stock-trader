package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** Opt-in PostgreSQL test for durable REAL fill observations. Does not call KIS. */
@EnabledIfEnvironmentVariable(named = "RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisRealFillSnapshotPostgresIntegrationTest {
    @Autowired private KisOrderIntentStore intents;
    @Autowired private KisRealFillSnapshotStore snapshots;
    @Autowired private KisOrderIntentRepository repository;
    @Autowired private JdbcTemplate jdbc;

    @Test
    void cumulativeRealFillObservationsSurviveReloadAndIgnoreDuplicateOrStaleSnapshots() {
        UUID key = UUID.randomUUID();
        try {
            intents.prepare(key, "f".repeat(64), KisEnvironment.REAL,
                    new OrderRequest(Market.KR, "005930", OrderSide.BUY, 3,
                            new BigDecimal("70000"), Currency.KRW), null);

            intents.reconcile(key, new KisBrokerOrderSnapshot(1, 2,
                    new BigDecimal("70000"), false, false));
            intents.reconcile(key, new KisBrokerOrderSnapshot(1, 2,
                    new BigDecimal("70000"), false, false));
            intents.reconcile(key, new KisBrokerOrderSnapshot(2, 1,
                    new BigDecimal("70050"), false, false));

            assertEquals(2, snapshots.forIntent(key).size());
            assertEquals(2, snapshots.forIntent(key).getLast().cumulativeFilledQuantity());
            assertEquals(0, snapshots.forIntent(key).getLast().cumulativeAverageFillPrice()
                    .compareTo(new BigDecimal("70050")));
            assertEquals(2, repository.findById(key).orElseThrow().getFilledQuantity());

            intents.reconcile(key, new KisBrokerOrderSnapshot(1, 2,
                    new BigDecimal("69900"), false, false));

            assertEquals(2, repository.findById(key).orElseThrow().getFilledQuantity());
            assertEquals(2, snapshots.forIntent(key).size());
            assertEquals(2, jdbc.queryForObject(
                    "SELECT COUNT(*) FROM kis_real_fill_snapshot WHERE idempotency_key = ?", Integer.class, key));
        } finally {
            repository.deleteById(key);
        }
    }
}
