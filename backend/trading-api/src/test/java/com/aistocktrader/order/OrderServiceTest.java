package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class OrderServiceTest {
    @Mock OrderRepository orders;
    private final UUID idempotencyKey = UUID.fromString("d8d449d8-f154-4c5a-b1d0-6ce69f790f51");
    private TradingSafetyProperties safeConfig() {
        return new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100"));
    }
    private OrderRequest request(BigDecimal price, int quantity) {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, quantity, price, Currency.KRW);
    }
    @Test
    void persistsOnlySimulatedOrdersWithinTheNotionalLimit() {
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty());
        when(orders.save(any(OrderRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));
        OrderRecord result = new OrderService(orders, safeConfig()).submit(request(new BigDecimal("70000"), 2), idempotencyKey);
        assertEquals("SIMULATED", result.getStatus());
        assertEquals("005930", result.getSymbol());
        assertEquals(idempotencyKey, result.getIdempotencyKey());
        verify(orders).save(any(OrderRecord.class));
    }
    @Test
    void rejectsAnOrderThatExceedsTheRiskLimitBeforePersistence() {
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty());
        assertThrows(IllegalArgumentException.class,
                () -> new OrderService(orders, safeConfig()).submit(request(new BigDecimal("600000"), 2), idempotencyKey));
        verify(orders, never()).save(any());
    }
    @Test
    void returnsTheExistingOrderWhenAnIdenticalRequestIsRetried() {
        OrderRequest request = request(new BigDecimal("70000"), 2);
        OrderRecord existing = new OrderRecord(request, idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.of(existing));
        OrderRecord result = new OrderService(orders, safeConfig()).submit(request, idempotencyKey);
        assertSame(existing, result);
        verify(orders, never()).save(any());
    }
    @Test
    void rejectsReusingTheSameKeyForADifferentOrder() {
        OrderRecord existing = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.of(existing));
        assertThrows(IdempotencyConflictException.class, () -> new OrderService(orders, safeConfig())
                .submit(request(new BigDecimal("71000"), 2), idempotencyKey));
        verify(orders, never()).save(any());
    }
    @Test
    void rejectsLiveTradingConfigurationAtStartup() {
        assertThrows(IllegalStateException.class,
                () -> new TradingSafetyProperties("paper", true, BigDecimal.TEN, BigDecimal.TEN));
        assertThrows(IllegalStateException.class,
                () -> new TradingSafetyProperties("live", false, BigDecimal.TEN, BigDecimal.TEN));
    }
}
