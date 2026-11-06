package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KisLiveOrderRiskGateTest {
    private final KisLiveOrderRiskGate gate = new KisLiveOrderRiskGate(
            new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100")),
            new KisLiveRiskLimits(new BigDecimal("1.00"), new BigDecimal("0.25"),
                    new BigDecimal("10.00"), 3, 3));

    @Test
    void approvesBuyOnlyWhenEveryLiveRiskInputIsPresentAndWithinLimits() {
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, true, true, false, 0,
                        new BigDecimal("0.50"), 0, new BigDecimal("1000000"), new BigDecimal("100")));

        assertTrue(result.approved());
        assertTrue(result.reasons().isEmpty());
    }

    @Test
    void dailyLossModelQuoteAndStopRequirementsBlockBeforeLiveOrder() {
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(false, false, true, true, false, 0,
                        BigDecimal.ONE, 3, new BigDecimal("1000000"), null));

        assertFalse(result.approved());
        assertTrue(result.reasons().containsAll(List.of("APPROVED_MODEL_CANDIDATE_REQUIRED", "FRESH_QUOTE_REQUIRED",
                "DAILY_LOSS_LIMIT", "CONSECUTIVE_LOSS_LIMIT", "VALID_STOP_DISTANCE_REQUIRED")));
    }

    @Test
    void negativeDailyLossPercentIsRejectedInsteadOfBeingTreatedAsNoLoss() {
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, true, true, false, 0,
                        new BigDecimal("-1.25"), 0, new BigDecimal("1000000"), new BigDecimal("100")));

        assertFalse(result.approved());
        assertTrue(result.reasons().contains("DAILY_LOSS_PERCENT_INVALID"));
    }

    @Test
    void positionExposureAndPerTradeRiskLimitsAreEnforced() {
        KisAccountSnapshot.Position position = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, new BigDecimal("70000"), new BigDecimal("95000"),
                new BigDecimal("95000"), BigDecimal.ZERO, BigDecimal.ZERO);
        KisLiveRiskDecision exposure = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of(position)),
                new KisLiveRiskContext(true, true, true, true, false, 1,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100")));
        KisLiveRiskDecision risk = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, true, true, false, 0,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("3000")));

        assertFalse(exposure.approved());
        assertTrue(exposure.reasons().contains("POSITION_EXPOSURE_LIMIT"));
        assertFalse(risk.approved());
        assertTrue(risk.reasons().contains("TRADE_RISK_LIMIT"));
    }

    @Test
    void newPositionsStopAtPortfolioLimitAndUnresolvedOrdersBlockEntries() {
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, true, true, true, 3,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100")));

        assertFalse(result.approved());
        assertTrue(result.reasons().containsAll(List.of("UNRESOLVED_ORDER_EXISTS", "OPEN_POSITION_LIMIT")));
    }

    @Test
    void sellCanReduceExposureWithoutAiEntryApprovalButRequiresFreshOwnedShares() {
        KisAccountSnapshot.Position held = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.valueOf(2), BigDecimal.valueOf(2), new BigDecimal("70000"), new BigDecimal("71000"),
                new BigDecimal("142000"), new BigDecimal("2000"), new BigDecimal("1.4"));
        KisLiveRiskDecision allowed = gate.assess(sell(2), null,
                account(KisEnvironment.REAL, "KRW", "0", List.of(held)),
                new KisLiveRiskContext(false, true, true, true, false, 1,
                        null, 3, null, null));
        KisLiveRiskDecision blocked = gate.assess(sell(3), null,
                account(KisEnvironment.REAL, "KRW", "0", List.of(held)),
                new KisLiveRiskContext(false, true, true, true, false, 1,
                        null, 3, null, null));

        assertTrue(allowed.approved());
        assertFalse(blocked.approved());
        assertEquals(List.of("INSUFFICIENT_SELLABLE_QUANTITY"), blocked.reasons());
    }

    @Test
    void paperAccountSnapshotAndStaleAccountBlockLiveAssessment() {
        KisLiveRiskContext healthy = new KisLiveRiskContext(true, true, true, true, false, 0,
                BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"));
        KisLiveRiskDecision paperAccount = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.PAPER, "KRW", "100000", List.of()), healthy);
        KisLiveRiskDecision staleAccount = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, false, true, false, 0,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100")));

        assertFalse(paperAccount.approved());
        assertTrue(paperAccount.reasons().contains("REAL_ACCOUNT_REQUIRED"));
        assertFalse(staleAccount.approved());
        assertTrue(staleAccount.reasons().contains("FRESH_ACCOUNT_SNAPSHOT_REQUIRED"));
    }

    @Test
    void nonKisAccountSnapshotSourceCannotAuthorizeLiveAssessment() {
        KisAccountSnapshot nonKisSource = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), List.of(), Instant.now(), "PAPER_LEDGER");
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null, nonKisSource,
                new KisLiveRiskContext(true, true, true, true, false, 0,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100")));

        assertFalse(result.approved());
        assertTrue(result.reasons().contains("KIS_ACCOUNT_SOURCE_REQUIRED"));
    }

    @Test
    void callerCannotMarkAnOldOrFutureDatedAccountSnapshotAsFresh() {
        KisLiveRiskContext healthy = new KisLiveRiskContext(true, true, true, true, false, 0,
                BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"));
        KisAccountSnapshot current = account(KisEnvironment.REAL, "KRW", "100000", List.of());
        KisAccountSnapshot stale = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), List.of(),
                Instant.now().minusSeconds(6), "KIS_READ_ONLY_REST");
        KisAccountSnapshot future = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), List.of(),
                Instant.now().plusSeconds(1), "KIS_READ_ONLY_REST");

        KisLiveRiskDecision staleResult = gate.assess(buy("005930", 1, "70000"), null, stale, healthy);
        KisLiveRiskDecision futureResult = gate.assess(buy("005930", 1, "70000"), null, future, healthy);

        assertFalse(staleResult.approved());
        assertTrue(staleResult.reasons().contains("FRESH_ACCOUNT_SNAPSHOT_REQUIRED"));
        assertFalse(futureResult.approved());
        assertTrue(futureResult.reasons().contains("FRESH_ACCOUNT_SNAPSHOT_REQUIRED"));
        assertTrue(gate.assess(buy("005930", 1, "70000"), null, current, healthy).approved());
    }

    private static OrderRequest buy(String symbol, int quantity, String price) {
        return new OrderRequest(Market.KR, symbol, OrderSide.BUY, quantity, new BigDecimal(price), Currency.KRW);
    }

    private static OrderRequest sell(int quantity) {
        return new OrderRequest(Market.KR, "005930", OrderSide.SELL, quantity,
                new BigDecimal("70000"), Currency.KRW);
    }

    private static KisAccountSnapshot account(KisEnvironment environment, String currency, String orderable,
                                               List<KisAccountSnapshot.Position> positions) {
        return new KisAccountSnapshot(environment, Market.KR, currency, new BigDecimal("100000"),
                new BigDecimal(orderable), positions, Instant.now(), "KIS_READ_ONLY_REST");
    }
}
