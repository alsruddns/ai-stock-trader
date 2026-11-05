package com.aistocktrader.health;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.OrderRecord;
import com.aistocktrader.order.OrderRepository;
import com.aistocktrader.order.OrderStatus;
import com.aistocktrader.order.PaperCashBalance;
import com.aistocktrader.order.PaperCashRepository;
import com.aistocktrader.order.PaperFillRepository;
import com.aistocktrader.order.PaperPosition;
import com.aistocktrader.order.PaperPositionRepository;
import com.aistocktrader.order.OrderSide;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Sort;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.EnumMap;
import java.util.List;
import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/operations")
public class OperationsController {
    private static final ZoneId SEOUL = ZoneId.of("Asia/Seoul");
    private static final List<OrderStatus> OPEN_STATUSES = List.of(OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING, OrderStatus.UNKNOWN);

    private final OrderRepository orders;
    private final PaperPositionRepository positions;
    private final PaperCashRepository cash;
    private final PaperFillRepository fills;
    private final TradingSafetyProperties safety;
    private final boolean kisAccountReadsEnabled;
    private final boolean kisPaperOrdersEnabled;

    public OperationsController(OrderRepository orders, PaperPositionRepository positions,
                                PaperCashRepository cash, PaperFillRepository fills,
                                TradingSafetyProperties safety,
                                @Value("${kis.account-read-enabled:false}") boolean kisAccountReadsEnabled,
                                @Value("${kis.orders.paper-enabled:false}") boolean kisPaperOrdersEnabled) {
        this.orders = orders;
        this.positions = positions;
        this.cash = cash;
        this.fills = fills;
        this.safety = safety;
        this.kisAccountReadsEnabled = kisAccountReadsEnabled;
        this.kisPaperOrdersEnabled = kisPaperOrdersEnabled;
    }

    @GetMapping("/summary")
    public OperationsSummary summary() {
        Instant now = Instant.now();
        Instant dayStart = LocalDate.now(SEOUL).atStartOfDay(SEOUL).toInstant();
        List<OrderRecord> allOrders = orders.findAll();
        EnumMap<OrderStatus, Long> counts = new EnumMap<>(OrderStatus.class);
        for (OrderStatus status : OrderStatus.values()) counts.put(status, 0L);
        allOrders.forEach(order -> counts.compute(order.getStatus(), (status, count) -> count == null ? 1L : count + 1));
        List<OrderView> recentOrders = orders.findAll(Sort.by(Sort.Direction.DESC, "createdAt")).stream()
                .limit(20).map(OrderView::from).toList();
        List<PositionView> openPositions = positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)
                .stream().map(PositionView::from).toList();
        List<CashView> cashBalances = cash.findAll().stream().map(CashView::from).toList();
        Map<String, BigDecimal> costBasis = new HashMap<>();
        openPositions.forEach(position -> costBasis.merge(position.currency(), position.costBasis(), BigDecimal::add));
        long fillsToday = fills.countByFilledAtAfter(dayStart);
        Map<String, BigDecimal> realizedPnlToday = realizedPnlSince(dayStart);
        int consecutiveLosingTrades = consecutiveLosingTrades();

        return new OperationsSummary(now, "paper", "disabled", false, kisAccountReadsEnabled,
                kisPaperOrdersEnabled, counts, consecutiveLosingTrades, counts.getOrDefault(OrderStatus.PENDING, 0L)
                + counts.getOrDefault(OrderStatus.PARTIALLY_FILLED, 0L)
                + counts.getOrDefault(OrderStatus.CANCEL_PENDING, 0L)
                + counts.getOrDefault(OrderStatus.UNKNOWN, 0L), fillsToday, allOrders.size(),
                cashBalances, openPositions, costBasis, realizedPnlToday, recentOrders);
    }

    private int consecutiveLosingTrades() {
        Map<String, Holding> holdings = new HashMap<>();
        Map<Long, BigDecimal> realizedBySellOrder = new java.util.LinkedHashMap<>();
        for (PaperFillRepository.FillTradeView trade : fills.findAllWithTradeDetails()) {
            String key = trade.getMarket() + "|" + trade.getSymbol() + "|" + trade.getCurrency();
            Holding holding = holdings.computeIfAbsent(key, ignored -> new Holding());
            BigDecimal quantity = BigDecimal.valueOf(trade.getQuantity());
            if (OrderSide.BUY.name().equals(trade.getSide())) {
                BigDecimal totalCost = holding.averageCost.multiply(BigDecimal.valueOf(holding.quantity))
                        .add(trade.getFillPrice().multiply(quantity));
                holding.quantity += trade.getQuantity();
                holding.averageCost = totalCost.divide(BigDecimal.valueOf(holding.quantity), 6, java.math.RoundingMode.HALF_UP);
            } else if (OrderSide.SELL.name().equals(trade.getSide())) {
                if (trade.getQuantity() > holding.quantity) {
                    throw new IllegalStateException("Paper fill history contains a sell larger than the recorded position.");
                }
                BigDecimal pnl = trade.getFillPrice().subtract(holding.averageCost).multiply(quantity);
                realizedBySellOrder.merge(trade.getOrderId(), pnl, BigDecimal::add);
                holding.quantity -= trade.getQuantity();
                if (holding.quantity == 0) holding.averageCost = BigDecimal.ZERO;
            }
        }
        int consecutive = 0;
        var sellResults = realizedBySellOrder.values().stream().toList();
        for (int index = sellResults.size() - 1; index >= 0; index--) {
            if (sellResults.get(index).signum() >= 0) break;
            consecutive++;
        }
        return consecutive;
    }

    private Map<String, BigDecimal> realizedPnlSince(Instant cutoff) {
        Map<String, Holding> holdings = new HashMap<>();
        Map<String, BigDecimal> realized = new HashMap<>(Map.of("KRW", BigDecimal.ZERO, "USD", BigDecimal.ZERO));
        for (PaperFillRepository.FillTradeView trade : fills.findAllWithTradeDetails()) {
            String key = trade.getMarket() + "|" + trade.getSymbol() + "|" + trade.getCurrency();
            Holding holding = holdings.computeIfAbsent(key, ignored -> new Holding());
            BigDecimal quantity = BigDecimal.valueOf(trade.getQuantity());
            if (OrderSide.BUY.name().equals(trade.getSide())) {
                BigDecimal totalCost = holding.averageCost.multiply(BigDecimal.valueOf(holding.quantity))
                        .add(trade.getFillPrice().multiply(quantity));
                holding.quantity += trade.getQuantity();
                holding.averageCost = totalCost.divide(BigDecimal.valueOf(holding.quantity), 6, java.math.RoundingMode.HALF_UP);
            } else if (OrderSide.SELL.name().equals(trade.getSide())) {
                if (trade.getQuantity() > holding.quantity) {
                    throw new IllegalStateException("Paper fill history contains a sell larger than the recorded position.");
                }
                if (!trade.getFilledAt().isBefore(cutoff)) {
                    BigDecimal pnl = trade.getFillPrice().subtract(holding.averageCost).multiply(quantity);
                    realized.merge(trade.getCurrency(), pnl, BigDecimal::add);
                }
                holding.quantity -= trade.getQuantity();
                if (holding.quantity == 0) holding.averageCost = BigDecimal.ZERO;
            }
        }
        return realized;
    }

    private static final class Holding {
        private int quantity;
        private BigDecimal averageCost = BigDecimal.ZERO;
    }

    public record OperationsSummary(Instant generatedAt, String tradingMode, String liveTrading,
                                    boolean paperAutomationEnabled, boolean kisAccountReadsEnabled,
                                    boolean kisPaperOrdersEnabled, Map<OrderStatus, Long> orderCounts,
                                    int consecutiveLosingTrades, long openOrders, long fillsToday, long totalOrders,
                                    List<CashView> cashBalances, List<PositionView> positions,
                                    Map<String, BigDecimal> positionCostBasisByCurrency,
                                    Map<String, BigDecimal> realizedPnlTodayGrossByCurrency,
                                    List<OrderView> recentOrders) { }

    public record CashView(String currency, BigDecimal balance) {
        static CashView from(PaperCashBalance item) { return new CashView(item.getCurrency().name(), item.getBalance()); }
    }

    public record PositionView(String market, String symbol, String currency, int quantity,
                               BigDecimal averageCost, BigDecimal costBasis) {
        static PositionView from(PaperPosition item) {
            return new PositionView(item.getMarket().name(), item.getSymbol(), item.getCurrency().name(),
                    item.getQuantity(), item.getAverageCost(), item.getAverageCost().multiply(BigDecimal.valueOf(item.getQuantity())));
        }
    }

    public record OrderView(long id, String market, String symbol, String side, int quantity,
                            int filledQuantity, BigDecimal limitPrice, String currency,
                            String status, Instant createdAt) {
        static OrderView from(OrderRecord item) {
            return new OrderView(item.getId(), item.getMarket().name(), item.getSymbol(), item.getSide().name(),
                    item.getQuantity(), item.getFilledQuantity(), item.getLimitPrice(), item.getCurrency().name(),
                    item.getStatus().name(), item.getCreatedAt());
        }
    }
}
