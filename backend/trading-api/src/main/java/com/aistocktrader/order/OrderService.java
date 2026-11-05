package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.math.BigDecimal;
import java.util.UUID;
import java.util.Locale;
import java.util.List;
import java.util.NoSuchElementException;

@Service
public class OrderService {
    private final OrderRepository orders;
    private final TradingSafetyProperties safety;
    private final PaperFillRepository fills;
    private final PaperPositionRepository positions;
    public OrderService(OrderRepository orders, TradingSafetyProperties safety,
                        PaperFillRepository fills, PaperPositionRepository positions) {
        this.orders = orders; this.safety = safety; this.fills = fills; this.positions = positions;
    }

    @Transactional
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
        return orders.save(new OrderRecord(request, idempotencyKey));
    }

    @Transactional
    public OrderRecord recordPaperFill(Long orderId, PaperFillRequest request) {
        requirePaperMode();
        var priorFill = fills.findByFillKey(request.fillKey());
        if (priorFill.isPresent()) {
            PaperFillRecord recorded = priorFill.get();
            if (!recorded.getOrderId().equals(orderId) || recorded.getQuantity() != request.quantity()
                    || recorded.getFillPrice().compareTo(request.fillPrice()) != 0) {
                throw new IdempotencyConflictException("Fill-Key was already used for a different fill.");
            }
            return orders.findById(orderId).orElseThrow(() -> new NoSuchElementException("Order not found."));
        }
        OrderRecord order = orders.findByIdForUpdate(orderId)
                .orElseThrow(() -> new NoSuchElementException("Order not found."));
        if (order.getSide() == OrderSide.SELL) {
            PaperPosition position = positions.findInstrumentForUpdate(order.getMarket(), order.getSymbol(), order.getCurrency())
                    .orElseThrow(() -> new IllegalArgumentException("Paper sell requires an existing position."));
            if (request.quantity() > position.getQuantity()) {
                throw new IllegalArgumentException("Paper sell exceeds the available position.");
            }
            order.applyFill(request.quantity(), request.fillPrice());
            position.apply(order.getSide(), request.quantity(), request.fillPrice());
            positions.save(position);
        } else {
            order.applyFill(request.quantity(), request.fillPrice());
            PaperPosition position = positions.findInstrumentForUpdate(order.getMarket(), order.getSymbol(), order.getCurrency())
                    .orElseGet(() -> new PaperPosition(order.getMarket(), order.getSymbol(), order.getCurrency()));
            position.apply(order.getSide(), request.quantity(), request.fillPrice());
            positions.save(position);
        }
        fills.save(new PaperFillRecord(order.getId(), request));
        return orders.save(order);
    }

    @Transactional(readOnly = true)
    public List<PaperPosition> paperPositions() {
        requirePaperMode();
        return positions.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0);
    }

    private void requirePaperMode() {
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("Paper broker operations are disabled outside paper mode.");
        }
    }

    private boolean matches(OrderRecord existing, OrderRequest request) {
        return existing.getMarket() == request.market()
                && existing.getSymbol().equals(request.symbol().trim().toUpperCase(Locale.ROOT))
                && existing.getSide() == request.side()
                && existing.getQuantity() == request.quantity()
                && existing.getLimitPrice().compareTo(request.limitPrice()) == 0
                && existing.getCurrency() == request.currency();
    }
}
