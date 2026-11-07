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
                new KisLiveRiskContext(true, true, true, true, false,
                        new BigDecimal("0.50"), 0, new BigDecimal("1000000"), new BigDecimal("100"), 0));

        assertTrue(result.approved());
        assertTrue(result.reasons().isEmpty());
    }

    @Test
    void rejectsBuyLimitAboveTheLatestTrustedQuote() {
        KisLiveRiskContext evidence = new KisLiveRiskContext(true, true, true, true, false,
                BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 0, false);

        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70001"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()), evidence);

        assertFalse(result.approved());
        assertTrue(result.reasons().contains("BUY_LIMIT_PRICE_ABOVE_LATEST_QUOTE"));
    }

    @Test
    void dailyLossModelQuoteAndStopRequirementsBlockBeforeLiveOrder() {
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(false, false, true, true, false,
                        BigDecimal.ONE, 3, new BigDecimal("1000000"), null, 0));

        assertFalse(result.approved());
        assertTrue(result.reasons().containsAll(List.of("APPROVED_MODEL_CANDIDATE_REQUIRED", "FRESH_QUOTE_REQUIRED",
                "DAILY_LOSS_LIMIT", "CONSECUTIVE_LOSS_LIMIT", "VALID_STOP_DISTANCE_REQUIRED")));
    }

    @Test
    void negativeDailyLossPercentIsRejectedInsteadOfBeingTreatedAsNoLoss() {
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, true, true, false,
                        new BigDecimal("-1.25"), 0, new BigDecimal("1000000"), new BigDecimal("100"), 0));

        assertFalse(result.approved());
        assertTrue(result.reasons().contains("DAILY_LOSS_PERCENT_INVALID"));
    }

    @Test
    void callerSuppliedEquityCannotReplaceMissingBrokerAccountEquity() {
        KisAccountSnapshot accountWithoutEquity = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), List.of(), Instant.now(), "KIS_READ_ONLY_REST");

        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null, accountWithoutEquity,
                new KisLiveRiskContext(true, true, true, true, false,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000000"), new BigDecimal("100"), 0));

        assertFalse(result.approved());
        assertTrue(result.reasons().contains("ACCOUNT_EQUITY_REQUIRED"));
    }

    @Test
    void positionExposureAndPerTradeRiskLimitsAreEnforced() {
        KisAccountSnapshot.Position position = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, new BigDecimal("70000"), new BigDecimal("95000"),
                new BigDecimal("95000"), BigDecimal.ZERO, BigDecimal.ZERO);
        KisLiveRiskDecision exposure = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of(position)),
                new KisLiveRiskContext(true, true, true, true, false,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 1));
        KisLiveRiskDecision risk = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, true, true, false,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("3000"), 0));

        assertFalse(exposure.approved());
        assertTrue(exposure.reasons().contains("POSITION_EXPOSURE_LIMIT"));
        assertFalse(risk.approved());
        assertTrue(risk.reasons().contains("TRADE_RISK_LIMIT"));
    }

    @Test
    void newPositionsStopAtPortfolioLimitAndUnresolvedOrdersBlockEntries() {
        List<KisAccountSnapshot.Position> heldPositions = List.of(
                heldOther("000001"), heldOther("000002"), heldOther("000003"));
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", heldPositions),
                new KisLiveRiskContext(true, true, true, true, true,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 3));

        assertFalse(result.approved());
        assertTrue(result.reasons().containsAll(List.of("UNRESOLVED_ORDER_EXISTS", "OPEN_POSITION_LIMIT")));
    }

    @Test
    void crossMarketPositionLimitRequiresAnAuthoritativeCombinedPositionCount() {
        KisAccountSnapshot usAccount = new KisAccountSnapshot(KisEnvironment.REAL, Market.US, "USD",
                new BigDecimal("100"), new BigDecimal("100"), new BigDecimal("1000"), List.of(),
                Instant.now(), "KIS_READ_ONLY_REST");
        OrderRequest usBuy = new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 1,
                new BigDecimal("10"), Currency.USD);
        KisLiveRiskContext completeThreePositions = new KisLiveRiskContext(true, true, true, true, false,
                BigDecimal.ZERO, 0, new BigDecimal("1000"), BigDecimal.ONE, 3);
        KisLiveRiskContext unknownCombinedCount = new KisLiveRiskContext(true, true, true, true, false,
                BigDecimal.ZERO, 0, new BigDecimal("1000"), BigDecimal.ONE, null);

        KisLiveRiskDecision atLimit = gate.assess(usBuy, "NASD", usAccount, completeThreePositions);
        KisLiveRiskDecision unknown = gate.assess(usBuy, "NASD", usAccount, unknownCombinedCount);

        assertFalse(atLimit.approved());
        assertTrue(atLimit.reasons().contains("OPEN_POSITION_LIMIT"));
        assertFalse(unknown.approved());
        assertTrue(unknown.reasons().contains("ALL_MARKET_POSITION_COUNT_REQUIRED"));
    }

    @Test
    void incompleteBrokerPositionQuantityOrValuationBlocksLiveAssessment() {
        KisAccountSnapshot.Position missingQuantity = new KisAccountSnapshot.Position("005930", "Samsung",
                null, BigDecimal.ZERO, null, null, null, null, null);
        KisAccountSnapshot.Position negativeQuantity = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE.negate(), BigDecimal.ZERO, null, null, null, null, null);
        KisAccountSnapshot.Position missingValuation = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, new BigDecimal("70000"), new BigDecimal("71000"),
                null, null, null);
        KisAccountSnapshot.Position missingSymbol = new KisAccountSnapshot.Position("", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, new BigDecimal("70000"), new BigDecimal("71000"),
                new BigDecimal("71000"), null, null);
        KisAccountSnapshot.Position negativeValuation = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, new BigDecimal("70000"), new BigDecimal("71000"),
                BigDecimal.ONE.negate(), null, null);

        for (KisAccountSnapshot.Position invalidPosition : List.of(missingQuantity, negativeQuantity,
                missingValuation, missingSymbol, negativeValuation)) {
            KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null,
                    account(KisEnvironment.REAL, "KRW", "100000", List.of(invalidPosition)),
                    new KisLiveRiskContext(true, true, true, true, false,
                            BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 1));

            assertFalse(result.approved());
            assertTrue(result.reasons().contains("ACCOUNT_POSITIONS_INVALID"));
        }
    }

    @Test
    void sellCanReduceExposureWithoutAiEntryApprovalButRequiresFreshOwnedShares() {
        KisAccountSnapshot.Position held = new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.valueOf(2), BigDecimal.valueOf(2), new BigDecimal("70000"), new BigDecimal("71000"),
                new BigDecimal("142000"), new BigDecimal("2000"), new BigDecimal("1.4"));
        KisLiveRiskDecision allowed = gate.assess(sell(2), null,
                account(KisEnvironment.REAL, "KRW", "0", List.of(held)),
                new KisLiveRiskContext(false, true, true, true, false,
                        null, 3, null, null, null));
        KisLiveRiskDecision blocked = gate.assess(sell(3), null,
                account(KisEnvironment.REAL, "KRW", "0", List.of(held)),
                new KisLiveRiskContext(false, true, true, true, false,
                        null, 3, null, null, null));

        assertTrue(allowed.approved());
        assertFalse(blocked.approved());
        assertEquals(List.of("INSUFFICIENT_SELLABLE_QUANTITY"), blocked.reasons());
    }

    @Test
    void paperAccountSnapshotAndStaleAccountBlockLiveAssessment() {
        KisLiveRiskContext healthy = new KisLiveRiskContext(true, true, true, true, false,
                BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 0);
        KisLiveRiskDecision paperAccount = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.PAPER, "KRW", "100000", List.of()), healthy);
        KisLiveRiskDecision staleAccount = gate.assess(buy("005930", 1, "70000"), null,
                account(KisEnvironment.REAL, "KRW", "100000", List.of()),
                new KisLiveRiskContext(true, true, false, true, false,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 0));

        assertFalse(paperAccount.approved());
        assertTrue(paperAccount.reasons().contains("REAL_ACCOUNT_REQUIRED"));
        assertFalse(staleAccount.approved());
        assertTrue(staleAccount.reasons().contains("FRESH_ACCOUNT_SNAPSHOT_REQUIRED"));
    }

    @Test
    void nonKisAccountSnapshotSourceCannotAuthorizeLiveAssessment() {
        KisAccountSnapshot nonKisSource = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), new BigDecimal("1000000"),
                List.of(), Instant.now(), "PAPER_LEDGER");
        KisLiveRiskDecision result = gate.assess(buy("005930", 1, "70000"), null, nonKisSource,
                new KisLiveRiskContext(true, true, true, true, false,
                        BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 0));

        assertFalse(result.approved());
        assertTrue(result.reasons().contains("KIS_ACCOUNT_SOURCE_REQUIRED"));
    }

    @Test
    void callerCannotMarkAnOldOrFutureDatedAccountSnapshotAsFresh() {
        KisLiveRiskContext healthy = new KisLiveRiskContext(true, true, true, true, false,
                BigDecimal.ZERO, 0, new BigDecimal("1000000"), new BigDecimal("100"), 0);
        KisAccountSnapshot current = account(KisEnvironment.REAL, "KRW", "100000", List.of());
        KisAccountSnapshot stale = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), new BigDecimal("1000000"), List.of(),
                Instant.now().minusSeconds(6), "KIS_READ_ONLY_REST");
        KisAccountSnapshot future = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                new BigDecimal("100000"), new BigDecimal("100000"), new BigDecimal("1000000"), List.of(),
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

    private static KisAccountSnapshot.Position heldOther(String symbol) {
        return new KisAccountSnapshot.Position(symbol, symbol, BigDecimal.ONE, BigDecimal.ONE,
                new BigDecimal("1000"), new BigDecimal("1000"), new BigDecimal("1000"),
                BigDecimal.ZERO, BigDecimal.ZERO);
    }

    private static KisAccountSnapshot account(KisEnvironment environment, String currency, String orderable,
                                               List<KisAccountSnapshot.Position> positions) {
        return new KisAccountSnapshot(environment, Market.KR, currency, new BigDecimal("100000"),
                new BigDecimal(orderable), new BigDecimal("1000000"), positions, Instant.now(), "KIS_READ_ONLY_REST");
    }
}
