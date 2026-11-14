package com.aistocktrader.kis;

import com.aistocktrader.order.OrderSide;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class KisDomesticCostReconcilerTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private final KisDomesticCostReconciler reconciler = new KisDomesticCostReconciler();
    private final KisRealizedPnlCalculator calculator = new KisRealizedPnlCalculator();

    @Test
    void matchesOnlyDocumentedDomesticCommissionAndTaxSummaryFields() throws Exception {
        var modeled = modeled();
        var summary = mapper.readTree("""
                {"buy_fee_smtl":"5","sll_fee_smtl":"5","sll_tltx_smtl":"2"}
                """);

        var result = reconciler.reconcile(summary, modeled, BigDecimal.ZERO);

        assertEquals(KisDomesticCostReconciler.Status.MATCHED, result.status());
        assertEquals(List.of(), result.unavailableFields());
        assertEquals(List.of(), result.mismatchedFields());
    }

    @Test
    void reportsMismatchedFieldNamesWithoutReturningFinancialValues() throws Exception {
        var summary = mapper.readTree("""
                {"buy_fee_smtl":"7","sll_fee_smtl":"5","sll_tltx_smtl":"2"}
                """);

        var result = reconciler.reconcile(summary, modeled(), BigDecimal.ZERO);

        assertEquals(KisDomesticCostReconciler.Status.MISMATCH, result.status());
        assertEquals(List.of("buy_fee_smtl"), result.mismatchedFields());
    }

    @Test
    void missingMalformedOrAmbiguousSummaryFailsClosed() throws Exception {
        var missing = reconciler.reconcile(mapper.readTree("{\"buy_fee_smtl\":\"1\"}"),
                modeled(), BigDecimal.ZERO);
        var malformed = reconciler.reconcile(mapper.readTree("{\"buy_fee_smtl\":\"NaN\",\"sll_fee_smtl\":\"1\",\"sll_tltx_smtl\":\"0\"}"),
                modeled(), BigDecimal.ZERO);
        var ambiguous = reconciler.reconcile(mapper.readTree("[{},{}]"), modeled(), BigDecimal.ZERO);

        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, missing.status());
        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, malformed.status());
        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, ambiguous.status());
    }

    @Test
    void acceptsSingleRecordSummaryArrayAndBoundedTolerance() throws Exception {
        var summary = mapper.readTree("""
                [{"buy_fee_smtl":"6","sll_fee_smtl":"5","sll_tltx_smtl":"2"}]
                """);

        var result = reconciler.reconcile(summary, modeled(), BigDecimal.ONE);

        assertEquals(KisDomesticCostReconciler.Status.MATCHED, result.status());
        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE,
                reconciler.reconcile(summary, modeled(), new BigDecimal("1001")).status());
    }

    @Test
    void comparesKisTotalRealizedPnlIncludingNegativeAmountsWithoutReturningMoney() throws Exception {
        var matching = reconciler.reconcileRealizedPnl(mapper.readTree("{\"tot_rlzt_pfls\":\"-12\"}"),
                new BigDecimal("-12.50"), BigDecimal.ONE);
        var mismatching = reconciler.reconcileRealizedPnl(mapper.readTree("{\"tot_rlzt_pfls\":\"-8\"}"),
                new BigDecimal("-12"), BigDecimal.ONE);

        assertEquals(KisDomesticCostReconciler.Status.MATCHED, matching.status());
        assertEquals(KisDomesticCostReconciler.Status.MISMATCH, mismatching.status());
        assertEquals(List.of("tot_rlzt_pfls"), mismatching.mismatchedFields());
    }

    @Test
    void missingOrMalformedRealizedPnlSummaryFailsClosed() throws Exception {
        var missing = reconciler.reconcileRealizedPnl(mapper.readTree("{}"), BigDecimal.ZERO, BigDecimal.ONE);
        var malformed = reconciler.reconcileRealizedPnl(mapper.readTree("{\"tot_rlzt_pfls\":\"NaN\"}"),
                BigDecimal.ZERO, BigDecimal.ONE);

        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, missing.status());
        assertEquals(List.of("tot_rlzt_pfls"), missing.unavailableFields());
        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, malformed.status());
    }

    private KisRealizedPnlCalculator.CostEstimate modeled() {
        return calculator.estimateCosts(List.of(
                fill("buy", OrderSide.BUY, "10000", 1),
                fill("sell", OrderSide.SELL, "10000", 2)),
                new KisRealizedPnlCalculator.Costs(new BigDecimal("5"), new BigDecimal("5"), new BigDecimal("2")));
    }

    private KisRealizedPnlCalculator.TradeFill fill(String id, OrderSide side, String price, int second) {
        return new KisRealizedPnlCalculator.TradeFill(id, "005930", side, 1,
                new BigDecimal(price), Instant.parse("2026-11-07T00:00:00Z").plusSeconds(second));
    }
}
