package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Read-only daily broker cost reconciliation; values are deliberately not persisted or returned. */
@Service
public class KisDomesticCostReconciliationService {
    private static final ZoneId KOREA = ZoneId.of("Asia/Seoul");
    private static final List<KisOrderStatus> TERMINAL = List.of(
            KisOrderStatus.FILLED, KisOrderStatus.CANCELLED, KisOrderStatus.REJECTED);

    private final KisProperties properties;
    private final KisAccountClient accounts;
    private final KisRealOrderClient brokerOrders;
    private final KisOrderIntentRepository intents;
    private final KisRealTradingCostProperties costs;
    private final KisRealizedPnlCalculator calculator = new KisRealizedPnlCalculator();
    private final KisDomesticCostReconciler reconciler = new KisDomesticCostReconciler();

    public KisDomesticCostReconciliationService(KisProperties properties, KisAccountClient accounts,
                                                 KisRealOrderClient brokerOrders,
                                                 KisOrderIntentRepository intents,
                                                 KisRealTradingCostProperties costs) {
        this.properties = properties;
        this.accounts = accounts;
        this.brokerOrders = brokerOrders;
        this.intents = intents;
        this.costs = costs;
    }

    public Reconciliation reconcile(LocalDate sessionDate) {
        if (sessionDate == null || !sessionDate.isBefore(LocalDate.now(KOREA))) {
            return Reconciliation.unavailable(sessionDate, List.of("session_not_closed"), 0);
        }
        if (!properties.accountReadEnabled() || !properties.realAccountReadEnabled()
                || !properties.credentials(KisEnvironment.REAL).configured()) {
            return Reconciliation.unavailable(sessionDate, List.of("real_account_read_disabled"), 0);
        }
        KisRealizedPnlCalculator.Costs configuredCosts = costs.forMarket(Market.KR);
        if (configuredCosts == null || !configuredCosts.valid()) {
            return Reconciliation.unavailable(sessionDate, List.of("real_costs_not_configured"), 0);
        }

        Instant from = sessionDate.atStartOfDay(KOREA).toInstant();
        Instant to = sessionDate.plusDays(1).atStartOfDay(KOREA).toInstant();
        List<KisOrderIntent> dayOrders = intents
                .findByEnvironmentAndMarketAndLastFillAtGreaterThanEqualAndLastFillAtLessThanOrderByLastFillAtAsc(
                        KisEnvironment.REAL, Market.KR, from, to);
        if (dayOrders == null || dayOrders.isEmpty()) {
            return Reconciliation.unavailable(sessionDate, List.of("no_local_fills"), 0);
        }

        List<KisRealizedPnlCalculator.TradeFill> fills = new ArrayList<>();
        Map<String, KisOrderIntent> localByBrokerOrder = new HashMap<>();
        Set<String> invalidReasons = new HashSet<>();
        for (KisOrderIntent order : dayOrders) {
            if (order == null || order.getEnvironment() != KisEnvironment.REAL || order.getMarket() != Market.KR
                    || order.getStatus() == null || !TERMINAL.contains(order.getStatus())
                    || order.getFilledQuantity() == null || order.getFilledQuantity() <= 0
                    || order.getAverageFillPrice() == null || order.getAverageFillPrice().signum() <= 0
                    || order.getLastFillAt() == null || order.getLastFillAt().isBefore(from)
                    || !order.getLastFillAt().isBefore(to)
                    || order.getBrokerOrderNumber() == null || order.getBrokerOrderNumber().isBlank()
                    || order.getBrokerOrganizationNumber() == null || order.getBrokerOrganizationNumber().isBlank()) {
                invalidReasons.add("incomplete_local_fill");
                continue;
            }
            String identity = identity(order.getBrokerOrganizationNumber(), order.getBrokerOrderNumber());
            if (localByBrokerOrder.putIfAbsent(identity, order) != null) {
                invalidReasons.add("duplicate_local_broker_order");
                continue;
            }
            fills.add(new KisRealizedPnlCalculator.TradeFill(order.getIdempotencyKey().toString(), order.getSymbol(),
                    order.getSide(), order.getFilledQuantity(), order.getAverageFillPrice(), order.getLastFillAt()));
        }
        if (!invalidReasons.isEmpty() || fills.size() != dayOrders.size()) {
            return Reconciliation.unavailable(sessionDate, sorted(invalidReasons), 0);
        }

        List<KisRealOrderClient.KisBrokerSessionExecution> brokerExecutions = brokerOrders
                .getRealSessionExecutions(Market.KR, sessionDate);
        if (brokerExecutions == null || brokerExecutions.isEmpty()) {
            return Reconciliation.unavailable(sessionDate, List.of("no_broker_executions"), 0);
        }
        Set<String> brokerIdentities = new HashSet<>();
        for (KisRealOrderClient.KisBrokerSessionExecution execution : brokerExecutions) {
            if (execution == null || execution.orderNumber() == null || execution.orderNumber().isBlank()
                    || execution.organizationNumber() == null || execution.organizationNumber().isBlank()) {
                return Reconciliation.unavailable(sessionDate, List.of("incomplete_broker_execution"), 0);
            }
            String identity = identity(execution.organizationNumber(), execution.orderNumber());
            KisOrderIntent local = localByBrokerOrder.get(identity);
            if (local == null || local.getSide() != execution.side()
                    || !local.getSymbol().equalsIgnoreCase(execution.symbol())
                    || !brokerIdentities.add(identity)) {
                return Reconciliation.unavailable(sessionDate, List.of("broker_execution_ledger_mismatch"), 0);
            }
        }
        if (!brokerIdentities.equals(localByBrokerOrder.keySet())) {
            return Reconciliation.unavailable(sessionDate, List.of("broker_execution_ledger_mismatch"), 0);
        }

        KisRealizedPnlCalculator.CostEstimate estimate = calculator.estimateCosts(fills, configuredCosts);
        if (!estimate.valid()) {
            return Reconciliation.unavailable(sessionDate, List.of("invalid_cost_estimate"), 0);
        }
        KisRealizedPnlStatement statement = accounts.getRealizedPnlStatement(Market.KR, sessionDate, sessionDate, null);
        if (statement == null || statement.summary() == null) {
            return Reconciliation.unavailable(sessionDate, List.of("broker_statement_unavailable"), fills.size());
        }
        BigDecimal roundingTolerance = BigDecimal.valueOf(Math.max(1, fills.size()));
        KisDomesticCostReconciler.Result comparison = reconciler.reconcile(statement.summary(), estimate,
                roundingTolerance);
        return new Reconciliation(sessionDate, comparison.status(), fills.size(),
                comparison.unavailableFields(), comparison.mismatchedFields());
    }

    private static String identity(String organization, String orderNumber) {
        return organization.trim() + ":" + orderNumber.trim();
    }

    private static List<String> sorted(Set<String> reasons) {
        if (reasons.isEmpty()) return List.of("incomplete_local_fill");
        return reasons.stream().sorted().toList();
    }

    public record Reconciliation(LocalDate sessionDate, KisDomesticCostReconciler.Status status,
                                 int matchedOrderCount, List<String> unavailableReasons,
                                 List<String> mismatchedFields) {
        public Reconciliation {
            unavailableReasons = List.copyOf(unavailableReasons);
            mismatchedFields = List.copyOf(mismatchedFields);
        }
        static Reconciliation unavailable(LocalDate date, List<String> reasons, int count) {
            return new Reconciliation(date, KisDomesticCostReconciler.Status.UNAVAILABLE, count, reasons, List.of());
        }
    }
}
