package com.aistocktrader.kis;

import com.aistocktrader.order.OrderSide;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Rebuilds session realized P&L from broker opening inventory and terminal, broker-confirmed orders. */
final class KisRealizedPnlCalculator {
    private static final BigDecimal TEN_THOUSAND = new BigDecimal("10000");

    Result calculate(List<OpeningPosition> openingPositions, List<TradeFill> fills, Costs costs) {
        if (openingPositions == null || fills == null || costs == null || !costs.valid()) return Result.invalid();
        Map<String, Inventory> inventory = new HashMap<>();
        for (OpeningPosition opening : openingPositions) {
            if (opening == null || opening.symbol() == null || opening.symbol().isBlank()
                    || opening.quantity() == null || opening.quantity().signum() <= 0
                    || opening.averagePrice() == null || opening.averagePrice().signum() <= 0) return Result.invalid();
            String symbol = normalize(opening.symbol());
            if (inventory.putIfAbsent(symbol, new Inventory(opening.quantity(),
                    opening.averagePrice().multiply(BigDecimal.ONE.add(costs.buyCommissionBps()
                            .divide(TEN_THOUSAND, 12, RoundingMode.HALF_UP))))) != null) return Result.invalid();
        }

        List<TradeFill> ordered = new ArrayList<>(fills);
        Set<String> fillIds = new HashSet<>();
        for (TradeFill fill : ordered) {
            if (fill == null || fill.id() == null || fill.id().isBlank() || !fillIds.add(fill.id())
                    || fill.symbol() == null || fill.symbol().isBlank() || fill.side() == null
                    || fill.quantity() <= 0 || fill.averagePrice() == null
                    || fill.averagePrice().signum() <= 0 || fill.executedAt() == null) return Result.invalid();
        }
        ordered.sort(Comparator.comparing(TradeFill::executedAt).thenComparing(fill -> fill.id().toString()));

        List<RealizedTrade> realized = new ArrayList<>();
        BigDecimal totalGrossRealizedPnl = BigDecimal.ZERO;
        BigDecimal totalNetRealizedPnl = BigDecimal.ZERO;
        BigDecimal estimatedBuyCommission = BigDecimal.ZERO;
        BigDecimal estimatedSellCommission = BigDecimal.ZERO;
        BigDecimal estimatedSellTax = BigDecimal.ZERO;
        for (TradeFill fill : ordered) {
            String symbol = normalize(fill.symbol());
            Inventory position = inventory.computeIfAbsent(symbol, ignored -> new Inventory());
            BigDecimal quantity = BigDecimal.valueOf(fill.quantity());
            BigDecimal notional = fill.averagePrice().multiply(quantity);
            if (fill.side() == OrderSide.BUY) {
                estimatedBuyCommission = estimatedBuyCommission.add(currencyCost(notional,
                        costs.buyCommissionBps()));
                BigDecimal unitCost = fill.averagePrice().multiply(BigDecimal.ONE.add(
                        costs.buyCommissionBps().divide(TEN_THOUSAND, 12, RoundingMode.HALF_UP)));
                position.buy(quantity, unitCost);
            } else {
                if (position.quantity.compareTo(quantity) < 0) return Result.invalid();
                BigDecimal gross = fill.averagePrice().subtract(position.averageCost).multiply(quantity);
                estimatedSellCommission = estimatedSellCommission.add(currencyCost(notional,
                        costs.sellCommissionBps()));
                estimatedSellTax = estimatedSellTax.add(currencyCost(notional, costs.sellTaxBps()));
                BigDecimal sellCost = notional.multiply(costs.sellCommissionBps()
                        .add(costs.sellTaxBps()).divide(TEN_THOUSAND, 12, RoundingMode.HALF_UP));
                BigDecimal net = gross.subtract(sellCost);
                position.sell(quantity);
                realized.add(new RealizedTrade(fill.id(), symbol, fill.executedAt(), gross, net));
                totalGrossRealizedPnl = totalGrossRealizedPnl.add(gross);
                totalNetRealizedPnl = totalNetRealizedPnl.add(net);
            }
        }

        int consecutiveLosses = 0;
        for (int index = realized.size() - 1; index >= 0; index--) {
            if (realized.get(index).netPnl().signum() >= 0) break;
            consecutiveLosses++;
        }
        Map<String, BigDecimal> openPositions = new HashMap<>();
        inventory.forEach((symbol, position) -> {
            if (position.quantity.signum() > 0) openPositions.put(symbol, position.quantity);
        });
        return new Result(true, consecutiveLosses, List.copyOf(realized), Map.copyOf(openPositions),
                estimatedBuyCommission, estimatedSellCommission, estimatedSellTax,
                totalGrossRealizedPnl, totalNetRealizedPnl);
    }

    CostEstimate estimateCosts(List<TradeFill> fills, Costs costs) {
        if (fills == null || costs == null || !costs.valid()) return CostEstimate.invalid();
        Set<String> fillIds = new HashSet<>();
        BigDecimal buyCommission = BigDecimal.ZERO;
        BigDecimal sellCommission = BigDecimal.ZERO;
        BigDecimal sellTax = BigDecimal.ZERO;
        for (TradeFill fill : fills) {
            if (fill == null || fill.id() == null || fill.id().isBlank() || !fillIds.add(fill.id())
                    || fill.symbol() == null || fill.symbol().isBlank() || fill.side() == null
                    || fill.quantity() <= 0 || fill.averagePrice() == null || fill.averagePrice().signum() <= 0
                    || fill.executedAt() == null) return CostEstimate.invalid();
            BigDecimal notional = fill.averagePrice().multiply(BigDecimal.valueOf(fill.quantity()));
            if (fill.side() == OrderSide.BUY) {
                buyCommission = buyCommission.add(currencyCost(notional, costs.buyCommissionBps()));
            } else {
                sellCommission = sellCommission.add(currencyCost(notional, costs.sellCommissionBps()));
                sellTax = sellTax.add(currencyCost(notional, costs.sellTaxBps()));
            }
        }
        return new CostEstimate(true, buyCommission, sellCommission, sellTax);
    }

    private static BigDecimal currencyCost(BigDecimal notional, BigDecimal rateBps) {
        return notional.multiply(rateBps).divide(TEN_THOUSAND, 0, RoundingMode.HALF_UP);
    }

    private static String normalize(String symbol) { return symbol.trim().toUpperCase(Locale.ROOT); }

    record OpeningPosition(String symbol, BigDecimal quantity, BigDecimal averagePrice) { }
    record TradeFill(String id, String symbol, OrderSide side, int quantity,
                     BigDecimal averagePrice, Instant executedAt) { }
    record Costs(BigDecimal buyCommissionBps, BigDecimal sellCommissionBps, BigDecimal sellTaxBps) {
        boolean valid() {
            return validRate(buyCommissionBps) && validRate(sellCommissionBps) && validRate(sellTaxBps);
        }
        private static boolean validRate(BigDecimal value) {
            return value != null && value.signum() >= 0 && value.compareTo(new BigDecimal("1000")) <= 0;
        }
    }
    record RealizedTrade(String id, String symbol, Instant executedAt, BigDecimal grossPnl, BigDecimal netPnl) { }
    record Result(boolean valid, int consecutiveLosses, List<RealizedTrade> trades,
                  Map<String, BigDecimal> openPositions, BigDecimal estimatedBuyCommission,
                  BigDecimal estimatedSellCommission, BigDecimal estimatedSellTax,
                  BigDecimal grossRealizedPnl, BigDecimal netRealizedPnl) {
        static Result invalid() {
            return new Result(false, -1, List.of(), Map.of(), null, null, null, null, null);
        }
    }
    record CostEstimate(boolean valid, BigDecimal buyCommission, BigDecimal sellCommission, BigDecimal sellTax) {
        static CostEstimate invalid() { return new CostEstimate(false, null, null, null); }
    }

    private static final class Inventory {
        private BigDecimal quantity;
        private BigDecimal averageCost;
        private Inventory() { this(BigDecimal.ZERO, BigDecimal.ZERO); }
        private Inventory(BigDecimal quantity, BigDecimal averageCost) {
            this.quantity = quantity;
            this.averageCost = averageCost;
        }
        private void buy(BigDecimal added, BigDecimal unitCost) {
            BigDecimal totalCost = quantity.multiply(averageCost).add(added.multiply(unitCost));
            quantity = quantity.add(added);
            averageCost = totalCost.divide(quantity, 12, RoundingMode.HALF_UP);
        }
        private void sell(BigDecimal removed) {
            quantity = quantity.subtract(removed);
            if (quantity.signum() == 0) averageCost = BigDecimal.ZERO;
        }
    }
}
