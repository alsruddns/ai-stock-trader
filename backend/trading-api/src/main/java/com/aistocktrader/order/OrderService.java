package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.math.BigDecimal;
import java.util.UUID;
import java.util.Locale;
import java.util.List;
import java.util.NoSuchElementException;
import java.time.Instant;

@Service
public class OrderService {
    private final OrderRepository orders;
    private final TradingSafetyProperties safety;
    private final PaperFillRepository fills;
    private final PaperPositionRepository positions;
    private final PaperCashRepository cashBalances;
    private final PaperStrategyRiskRepository strategyRisks;
    private final PaperTransactionCostCalculator costs;
    private final PaperOrderAmendmentRepository amendments;
    @Autowired
    public OrderService(OrderRepository orders, TradingSafetyProperties safety,
                        PaperFillRepository fills, PaperPositionRepository positions, PaperCashRepository cashBalances,
                        PaperStrategyRiskRepository strategyRisks, PaperTransactionCostCalculator costs,
                        PaperOrderAmendmentRepository amendments) {
        this.orders = orders; this.safety = safety; this.fills = fills;
        this.positions = positions; this.cashBalances = cashBalances;
        this.strategyRisks = strategyRisks; this.costs = costs; this.amendments = amendments;
    }
    public OrderService(OrderRepository orders, TradingSafetyProperties safety,
                        PaperFillRepository fills, PaperPositionRepository positions, PaperCashRepository cashBalances,
                        PaperStrategyRiskRepository strategyRisks) {
        this(orders, safety, fills, positions, cashBalances, strategyRisks, disabledCosts(), null);
    }
    public OrderService(OrderRepository orders, TradingSafetyProperties safety,
                        PaperFillRepository fills, PaperPositionRepository positions, PaperCashRepository cashBalances,
                        PaperStrategyRiskRepository strategyRisks, PaperOrderAmendmentRepository amendments) {
        this(orders, safety, fills, positions, cashBalances, strategyRisks, disabledCosts(), amendments);
    }
    public OrderService(OrderRepository orders, TradingSafetyProperties safety,
                        PaperFillRepository fills, PaperPositionRepository positions, PaperCashRepository cashBalances) {
        this(orders, safety, fills, positions, cashBalances, null, disabledCosts(), null);
    }

    public OrderService(OrderRepository orders, TradingSafetyProperties safety,
                        PaperFillRepository fills, PaperPositionRepository positions, PaperCashRepository cashBalances,
                        PaperStrategyRiskRepository strategyRisks, PaperTransactionCostCalculator costs) {
        this(orders, safety, fills, positions, cashBalances, strategyRisks, costs, null);
    }

    public OrderRecord submit(OrderRequest request, UUID idempotencyKey) {
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("Only paper orders are available.");
        }
        if (request.quantity() <= 0 || request.limitPrice() == null || request.limitPrice().signum() <= 0) {
            throw new IllegalArgumentException("Quantity and limit price must be positive.");
        }
        var existing = orders.findByIdempotencyKey(idempotencyKey);
        if (existing.isPresent()) {
            if (matches(existing.get(), request)) return existing.get();
            throw new IdempotencyConflictException("Idempotency-Key was already used for a different order.");
        }
        if ((request.market() == Market.KR && request.currency() != Currency.KRW)
                || (request.market() == Market.US && request.currency() != Currency.USD)) {
            throw new IllegalArgumentException("Currency must match the selected market.");
        }
        if ((request.market() == Market.KR && !request.symbol().matches("\\d{6}"))
                || (request.market() == Market.US && !request.symbol().matches("(?i)[A-Z0-9.-]{1,16}"))) {
            throw new IllegalArgumentException("Symbol format does not match the selected market.");
        }
        BigDecimal notional = request.limitPrice().multiply(BigDecimal.valueOf(request.quantity()));
        BigDecimal limit = request.currency() == Currency.KRW
                ? safety.maxOrderNotionalKrw() : safety.maxOrderNotionalUsd();
        if (notional.compareTo(limit) > 0) {
            throw new IllegalArgumentException("Order exceeds the configured paper-trading notional limit.");
        }
        orders.insertIfIdempotencyKeyAbsent(request.market().name(), request.symbol().trim().toUpperCase(Locale.ROOT),
                request.side().name(), request.quantity(), request.limitPrice(), request.currency().name(),
                request.strategyId(), request.modelId(), idempotencyKey);
        var persisted = orders.findByIdempotencyKey(idempotencyKey)
                .orElseThrow(() -> new IllegalStateException("Paper order insert did not produce a readable row."));
        if (!matches(persisted, request)) {
            throw new IdempotencyConflictException("Idempotency-Key was concurrently used for a different order.");
        }
        return persisted;
    }

    @Transactional
    public OrderRecord recordPaperFill(Long orderId, PaperFillRequest request) {
        requirePaperMode();
        var priorFill = fills.findByFillKey(request.fillKey());
        if (priorFill.isPresent()) {
            PaperFillRecord recorded = priorFill.get();
            if (!recorded.getOrderId().equals(orderId) || recorded.getQuantity() != request.quantity()
                    || recorded.getFillPrice().compareTo(request.fillPrice()) != 0
                    || !samePrice(recorded.getStrategyStopPrice(), request.stopPrice())
                    || !samePrice(recorded.getStrategyTargetPrice(), request.targetPrice())) {
                throw new IdempotencyConflictException("Fill-Key was already used for a different fill.");
            }
            return orders.findById(orderId).orElseThrow(() -> new NoSuchElementException("Order not found."));
        }
        OrderRecord order = orders.findByIdForUpdate(orderId)
                .orElseThrow(() -> new NoSuchElementException("Order not found."));
        validateStrategyRisk(order, request);
        PaperCashBalance cash = cashBalances.findByCurrencyForUpdate(order.getCurrency())
                .orElseThrow(() -> new IllegalStateException("Paper cash account is not initialized."));
        BigDecimal fillNotional = request.fillPrice().multiply(BigDecimal.valueOf(request.quantity()));
        BigDecimal transactionCost = costs.calculate(order.getMarket(), order.getSide(), order.getCurrency(), fillNotional);
        if (order.getSide() == OrderSide.SELL) {
            PaperPosition position = positions.findInstrumentForUpdate(order.getMarket(), order.getSymbol(), order.getCurrency())
                    .orElseThrow(() -> new IllegalArgumentException("Paper sell requires an existing position."));
            if (request.quantity() > position.getQuantity()) {
                throw new IllegalArgumentException("Paper sell exceeds the available position.");
            }
            order.applyFill(request.quantity(), request.fillPrice());
            position.apply(order.getSide(), request.quantity(), request.fillPrice());
            cash.credit(fillNotional.subtract(transactionCost));
            positions.save(position);
            if (position.getQuantity() == 0 && strategyRisks != null) {
                strategyRisks.deleteByPositionId(position.getId());
            }
        } else {
            BigDecimal debit = fillNotional.add(transactionCost);
            if (cash.getBalance().compareTo(debit) < 0) {
                throw new IllegalArgumentException("Paper cash balance is insufficient after transaction costs.");
            }
            order.applyFill(request.quantity(), request.fillPrice());
            cash.debit(debit);
            PaperPosition position = positions.findInstrumentForUpdate(order.getMarket(), order.getSymbol(), order.getCurrency())
                    .orElseGet(() -> new PaperPosition(order.getMarket(), order.getSymbol(), order.getCurrency()));
            BigDecimal costPerShare = transactionCost.divide(BigDecimal.valueOf(request.quantity()), 6,
                    java.math.RoundingMode.HALF_UP);
            position.apply(order.getSide(), request.quantity(), request.fillPrice().add(costPerShare));
            PaperPosition savedPosition = positions.save(position);
            if (request.stopPrice() != null && strategyRisks != null) {
                PaperStrategyRisk risk = strategyRisks.findByPositionId(savedPosition.getId())
                        .orElseGet(() -> new PaperStrategyRisk(savedPosition.getId(), request.stopPrice(), request.targetPrice(),
                                order.getStrategyId(), order.getModelId()));
                risk.update(request.stopPrice(), request.targetPrice(), order.getStrategyId(), order.getModelId());
                strategyRisks.save(risk);
            }
        }
        cashBalances.save(cash);
        fills.save(new PaperFillRecord(order.getId(), request, transactionCost));
        return orders.save(order);
    }

    @Transactional
    public OrderRecord cancelPaperOrder(Long orderId) {
        requirePaperMode();
        OrderRecord order = orders.findByIdForUpdate(orderId)
                .orElseThrow(() -> new NoSuchElementException("Order not found."));
        if (order.getStatus() == OrderStatus.CANCELLED) return order;
        order.cancelPaperOrder();
        return orders.save(order);
    }

    @Transactional
    public OrderRecord amendPaperOrder(Long orderId, UUID idempotencyKey, PaperOrderAmendmentRequest request) {
        requirePaperMode();
        if (idempotencyKey == null) throw new IllegalArgumentException("Amendment Idempotency-Key is required.");
        OrderRecord order = orders.findByIdForUpdate(orderId)
                .orElseThrow(() -> new NoSuchElementException("Order not found."));
        PaperOrderAmendment previous = amendments.findByOrderIdAndIdempotencyKey(orderId, idempotencyKey).orElse(null);
        if (previous != null) {
            if (previous.getNewQuantity() == request.quantity()
                    && previous.getNewLimitPrice().compareTo(request.limitPrice()) == 0) return order;
            throw new IdempotencyConflictException("Amendment Idempotency-Key was already used for different values.");
        }
        BigDecimal notional = request.limitPrice().multiply(BigDecimal.valueOf(request.quantity()));
        BigDecimal limit = order.getCurrency() == Currency.KRW
                ? safety.maxOrderNotionalKrw() : safety.maxOrderNotionalUsd();
        if (notional.compareTo(limit) > 0) {
            throw new IllegalArgumentException("Amended paper order exceeds the configured notional limit.");
        }
        int oldQuantity = order.getQuantity();
        BigDecimal oldLimitPrice = order.getLimitPrice();
        order.amendPaperOrder(request.quantity(), request.limitPrice());
        PaperOrderAmendment amendment = new PaperOrderAmendment(orderId, idempotencyKey,
                oldQuantity, request.quantity(), oldLimitPrice, request.limitPrice());
        amendments.save(amendment);
        order.amendPaperOrder(request.quantity(), request.limitPrice());
        return orders.save(order);
    }

    @Transactional
    public OrderRecord expirePaperOrder(Long orderId) {
        requirePaperMode();
        OrderRecord order = orders.findByIdForUpdate(orderId)
                .orElseThrow(() -> new NoSuchElementException("Order not found."));
        order.expirePaperOrder();
        return orders.save(order);
    }

    @Transactional(readOnly = true)
    public OrderRecord getPaperOrder(Long orderId) {
        requirePaperMode();
        return orders.findById(orderId).orElseThrow(() -> new NoSuchElementException("Order not found."));
    }

    @Transactional(readOnly = true)
    public OrderRecord getPaperOrderByIdempotencyKey(UUID idempotencyKey) {
        requirePaperMode();
        return orders.findByIdempotencyKey(idempotencyKey)
                .orElseThrow(() -> new NoSuchElementException("Order not found."));
    }

    @Transactional
    public int expireStalePaperOrders(long timeoutSeconds) {
        requirePaperMode();
        if (timeoutSeconds <= 0) return 0;
        Instant cutoff = Instant.now().minusSeconds(timeoutSeconds);
        var stale = orders.findOpenOrdersCreatedBefore(
                List.of(OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED), cutoff);
        stale.forEach(OrderRecord::expirePaperOrder);
        orders.saveAll(stale);
        return stale.size();
    }

    @Transactional(readOnly = true)
    public List<PaperPosition> paperPositions() {
        requirePaperMode();
        return positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0);
    }

    @Transactional(readOnly = true)
    public List<PaperCashBalance> paperCashBalances() {
        requirePaperMode();
        return cashBalances.findAll();
    }

    @Transactional(readOnly = true)
    public List<StrategyPositionRiskView> paperStrategyRisks() {
        requirePaperMode();
        if (strategyRisks == null) return List.of();
        return strategyRisks.findAllByOrderByUpdatedAtAsc().stream()
                .map(risk -> positions.findById(risk.getPositionId())
                        .filter(position -> position.getQuantity() > 0)
                        .map(position -> new StrategyPositionRiskView(position.getMarket().name(), position.getSymbol(),
                                position.getCurrency().name(), position.getQuantity(), position.getAverageCost(),
                                risk.getStopPrice(), risk.getTargetPrice(), risk.getOpenedAt(), risk.getUpdatedAt(),
                                risk.getStrategyId(), risk.getModelId()))
                        .orElse(null))
                .filter(java.util.Objects::nonNull)
                .toList();
    }

    private void validateStrategyRisk(OrderRecord order, PaperFillRequest request) {
        boolean hasStop = request.stopPrice() != null;
        boolean hasTarget = request.targetPrice() != null;
        if (hasStop != hasTarget) {
            throw new IllegalArgumentException("Strategy stop and target prices must be provided together.");
        }
        if (!hasStop) return;
        if (order.getSide() != OrderSide.BUY || strategyRisks == null) {
            throw new IllegalArgumentException("Strategy exit prices are supported only for paper buys.");
        }
        if (request.stopPrice().compareTo(request.fillPrice()) >= 0
                || request.targetPrice().compareTo(request.fillPrice()) <= 0) {
            throw new IllegalArgumentException("Strategy stop must be below and target above the entry fill price.");
        }
    }

    private static boolean samePrice(BigDecimal left, BigDecimal right) {
        return left == null ? right == null : right != null && left.compareTo(right) == 0;
    }

    private void requirePaperMode() {
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("Paper broker operations are disabled outside paper mode.");
        }
    }

    private static PaperTransactionCostCalculator disabledCosts() {
        return new PaperTransactionCostCalculator(new PaperCostProperties(false, BigDecimal.ZERO, BigDecimal.ZERO,
                BigDecimal.ZERO, BigDecimal.ZERO));
    }

    private boolean matches(OrderRecord existing, OrderRequest request) {
        return existing.getMarket() == request.market()
                && existing.getSymbol().equals(request.symbol().trim().toUpperCase(Locale.ROOT))
                && existing.getSide() == request.side()
                && existing.getQuantity() == request.quantity()
                && existing.getLimitPrice().compareTo(request.limitPrice()) == 0
                && existing.getCurrency() == request.currency()
                && java.util.Objects.equals(existing.getStrategyId(), request.strategyId())
                && java.util.Objects.equals(existing.getModelId(), request.modelId());
    }

    public record StrategyPositionRiskView(String market, String symbol, String currency, int quantity,
                                           BigDecimal averageCost, BigDecimal stopPrice, BigDecimal targetPrice,
                                           Instant openedAt, Instant updatedAt, String strategyId, String modelId) { }
}
