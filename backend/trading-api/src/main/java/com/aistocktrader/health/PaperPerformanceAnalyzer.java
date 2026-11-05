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
                AttributionKey attribution = new AttributionKey(trade.getMarket(), trade.getSymbol(),
                        trade.getCurrency(), trade.getStrategyId(), trade.getModelId());
                SellOrder sell = sellOrders.computeIfAbsent(trade.getOrderId(), ignored -> new SellOrder(key, attribution));
                if (!sell.instrumentKey.equals(key) || !sell.attribution.equals(attribution)) {
                    throw new IllegalStateException("A paper sell order contains inconsistent instrument or strategy attribution.");
                }
                sell.pnl = sell.pnl.add(pnl);
                sell.lastFilledAt = trade.getFilledAt();
                holding.quantity -= trade.getQuantity();
                if (holding.quantity == 0) holding.averageCost = BigDecimal.ZERO;
            }
        }

        Map<AttributionKey, Aggregate> totals = new LinkedHashMap<>();
        sellOrders.values().stream().sorted(Comparator.comparing(sell -> sell.lastFilledAt))
                .forEach(sell -> totals.computeIfAbsent(sell.attribution, ignored -> new Aggregate()).add(sell.pnl));
        return totals.entrySet().stream().map(entry -> {
            AttributionKey key = entry.getKey();
            return entry.getValue().toView(key.market, key.symbol, key.currency, key.strategyId, key.modelId);
        }).sorted(Comparator.comparing(InstrumentPerformance::market)
                .thenComparing(InstrumentPerformance::symbol).thenComparing(InstrumentPerformance::currency)
                .thenComparing(item -> item.strategyId() == null ? "" : item.strategyId())
                .thenComparing(item -> item.modelId() == null ? "" : item.modelId())).toList();
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
        private final String instrumentKey;
        private final AttributionKey attribution;
        private BigDecimal pnl = BigDecimal.ZERO;
        private java.time.Instant lastFilledAt;
        private SellOrder(String instrumentKey, AttributionKey attribution) {
            this.instrumentKey = instrumentKey;
            this.attribution = attribution;
        }
    }

    private record AttributionKey(String market, String symbol, String currency, String strategyId, String modelId) { }

    private static final class Aggregate {
        private long realizedSellOrders;
        private long wins;
        private long losses;
        private BigDecimal netPnl = BigDecimal.ZERO;
        private BigDecimal winningPnl = BigDecimal.ZERO;
        private BigDecimal losingPnl = BigDecimal.ZERO;
        private BigDecimal grossProfit = BigDecimal.ZERO;
        private BigDecimal grossLoss = BigDecimal.ZERO;
        private BigDecimal realizedEquity = BigDecimal.ZERO;
        private BigDecimal peakRealizedEquity = BigDecimal.ZERO;
        private BigDecimal maxRealizedDrawdown = BigDecimal.ZERO;

        private void add(BigDecimal pnl) {
            realizedSellOrders++;
            netPnl = netPnl.add(pnl);
            if (pnl.signum() > 0) {
                wins++; winningPnl = winningPnl.add(pnl); grossProfit = grossProfit.add(pnl);
            } else if (pnl.signum() < 0) {
                losses++; losingPnl = losingPnl.add(pnl); grossLoss = grossLoss.add(pnl.abs());
            }
            realizedEquity = realizedEquity.add(pnl);
            peakRealizedEquity = peakRealizedEquity.max(realizedEquity);
            maxRealizedDrawdown = maxRealizedDrawdown.max(peakRealizedEquity.subtract(realizedEquity));
        }

        private InstrumentPerformance toView(String market, String symbol, String currency,
                                             String strategyId, String modelId) {
            BigDecimal averageWin = wins == 0 ? BigDecimal.ZERO : winningPnl.divide(BigDecimal.valueOf(wins), 6, RoundingMode.HALF_UP);
            BigDecimal averageLoss = losses == 0 ? BigDecimal.ZERO : losingPnl.divide(BigDecimal.valueOf(losses), 6, RoundingMode.HALF_UP);
            BigDecimal winRate = realizedSellOrders == 0 ? BigDecimal.ZERO
                    : BigDecimal.valueOf(wins).divide(BigDecimal.valueOf(realizedSellOrders), 6, RoundingMode.HALF_UP);
            BigDecimal expectancy = realizedSellOrders == 0 ? BigDecimal.ZERO
                    : netPnl.divide(BigDecimal.valueOf(realizedSellOrders), 6, RoundingMode.HALF_UP);
            BigDecimal profitFactor = grossLoss.signum() == 0 ? null
                    : grossProfit.divide(grossLoss, 6, RoundingMode.HALF_UP);
            return new InstrumentPerformance(market, symbol, currency, strategyId, modelId,
                    realizedSellOrders, wins, losses, netPnl, averageWin, averageLoss, winRate,
                    expectancy, profitFactor, maxRealizedDrawdown);
        }
    }

    public record InstrumentPerformance(String market, String symbol, String currency, String strategyId,
                                        String modelId, long realizedSellOrders, long wins, long losses,
                                        BigDecimal netPnl, BigDecimal averageWin, BigDecimal averageLoss,
                                        BigDecimal winRate, BigDecimal expectancy, BigDecimal profitFactor,
                                        BigDecimal maxRealizedDrawdown) { }
}
