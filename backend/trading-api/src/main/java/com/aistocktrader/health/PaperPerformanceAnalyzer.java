package com.aistocktrader.health;

import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.PaperFillRepository;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Replays the local paper ledger at average cost and reports realized sell-order outcomes. */
public final class PaperPerformanceAnalyzer {
    public List<InstrumentPerformance> analyze(List<PaperFillRepository.FillTradeView> trades) {
        Map<String, Holding> holdings = new HashMap<>();
        Map<Long, SellOrder> sellOrders = new LinkedHashMap<>();
        for (PaperFillRepository.FillTradeView trade : trades) {
            String key = key(trade.getMarket(), trade.getSymbol(), trade.getCurrency());
            Holding holding = holdings.computeIfAbsent(key, ignored -> new Holding());
            BigDecimal quantity = BigDecimal.valueOf(trade.getQuantity());
            BigDecimal transactionCost = zeroIfNull(trade.getTransactionCost());
            if (OrderSide.BUY.name().equals(trade.getSide())) {
                BigDecimal totalCost = holding.averageCost.multiply(BigDecimal.valueOf(holding.quantity))
                        .add(trade.getFillPrice().multiply(quantity)).add(transactionCost);
                holding.quantity += trade.getQuantity();
                holding.averageCost = totalCost.divide(BigDecimal.valueOf(holding.quantity), 6, RoundingMode.HALF_UP);
            } else if (OrderSide.SELL.name().equals(trade.getSide())) {
                if (trade.getQuantity() > holding.quantity) {
                    throw new IllegalStateException("Paper fill history contains a sell larger than the recorded position.");
                }
                BigDecimal pnl = trade.getFillPrice().subtract(holding.averageCost).multiply(quantity).subtract(transactionCost);
                SellOrder sell = sellOrders.computeIfAbsent(trade.getOrderId(), ignored ->
                        new SellOrder(trade.getMarket(), trade.getSymbol(), trade.getCurrency()));
                if (!sell.key.equals(key)) {
                    throw new IllegalStateException("A paper sell order contains fills for more than one instrument.");
                }
                sell.pnl = sell.pnl.add(pnl);
                holding.quantity -= trade.getQuantity();
                if (holding.quantity == 0) holding.averageCost = BigDecimal.ZERO;
            }
        }

        Map<String, Aggregate> totals = new LinkedHashMap<>();
        sellOrders.values().forEach(sell -> totals.computeIfAbsent(sell.key, ignored -> new Aggregate()).add(sell.pnl));
        return totals.entrySet().stream().map(entry -> {
            String[] parts = entry.getKey().split("\\|", 3);
            return entry.getValue().toView(parts[0], parts[1], parts[2]);
        }).sorted(Comparator.comparing(InstrumentPerformance::market)
                .thenComparing(InstrumentPerformance::symbol).thenComparing(InstrumentPerformance::currency)).toList();
    }

    private static String key(String market, String symbol, String currency) {
        return market + "|" + symbol + "|" + currency;
    }

    private static BigDecimal zeroIfNull(BigDecimal value) { return value == null ? BigDecimal.ZERO : value; }

    private static final class Holding {
        private int quantity;
        private BigDecimal averageCost = BigDecimal.ZERO;
    }

    private static final class SellOrder {
        private final String key;
        private BigDecimal pnl = BigDecimal.ZERO;
        private SellOrder(String market, String symbol, String currency) { this.key = key(market, symbol, currency); }
    }

    private static final class Aggregate {
        private long realizedSellOrders;
        private long wins;
        private long losses;
        private BigDecimal netPnl = BigDecimal.ZERO;
        private BigDecimal winningPnl = BigDecimal.ZERO;
        private BigDecimal losingPnl = BigDecimal.ZERO;

        private void add(BigDecimal pnl) {
            realizedSellOrders++;
            netPnl = netPnl.add(pnl);
            if (pnl.signum() > 0) { wins++; winningPnl = winningPnl.add(pnl); }
            else if (pnl.signum() < 0) { losses++; losingPnl = losingPnl.add(pnl); }
        }

        private InstrumentPerformance toView(String market, String symbol, String currency) {
            BigDecimal averageWin = wins == 0 ? BigDecimal.ZERO : winningPnl.divide(BigDecimal.valueOf(wins), 6, RoundingMode.HALF_UP);
            BigDecimal averageLoss = losses == 0 ? BigDecimal.ZERO : losingPnl.divide(BigDecimal.valueOf(losses), 6, RoundingMode.HALF_UP);
            BigDecimal winRate = realizedSellOrders == 0 ? BigDecimal.ZERO
                    : BigDecimal.valueOf(wins).divide(BigDecimal.valueOf(realizedSellOrders), 6, RoundingMode.HALF_UP);
            return new InstrumentPerformance(market, symbol, currency, realizedSellOrders, wins, losses,
                    netPnl, averageWin, averageLoss, winRate);
        }
    }

    public record InstrumentPerformance(String market, String symbol, String currency, long realizedSellOrders,
                                        long wins, long losses, BigDecimal netPnl, BigDecimal averageWin,
                                        BigDecimal averageLoss, BigDecimal winRate) { }
}
