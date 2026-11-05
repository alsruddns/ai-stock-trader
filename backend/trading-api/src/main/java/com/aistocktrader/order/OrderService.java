package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.math.BigDecimal;
import java.util.UUID;
import java.util.Locale;

@Service
public class OrderService {
    private final OrderRepository orders;
    private final TradingSafetyProperties safety;
    public OrderService(OrderRepository orders, TradingSafetyProperties safety) {
        this.orders = orders; this.safety = safety;
    }

    @Transactional
    public OrderRecord submit(OrderRequest request, UUID idempotencyKey) {
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("Only simulated orders are available in P1.");
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

    private boolean matches(OrderRecord existing, OrderRequest request) {
        return existing.getMarket() == request.market()
                && existing.getSymbol().equals(request.symbol().trim().toUpperCase(Locale.ROOT))
                && existing.getSide() == request.side()
                && existing.getQuantity() == request.quantity()
                && existing.getLimitPrice().compareTo(request.limitPrice()) == 0
                && existing.getCurrency() == request.currency();
    }
}
