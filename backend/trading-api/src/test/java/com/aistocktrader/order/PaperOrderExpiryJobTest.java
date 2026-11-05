package com.aistocktrader.order;

import com.aistocktrader.config.PaperTradingProperties;
import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

class PaperOrderExpiryJobTest {
    @Test
    void expiresOrdersOnlyInEnabledPaperMode() {
        OrderService orders = mock(OrderService.class);
        PaperOrderExpiryJob job = new PaperOrderExpiryJob(
                new PaperTradingProperties(BigDecimal.ZERO, BigDecimal.ZERO, 30), orders,
                new TradingSafetyProperties("paper", false, false, new BigDecimal("1000000"), new BigDecimal("100")));

        job.expireStaleOrders();

        verify(orders).expireStalePaperOrders(30);
    }

    @Test
    void doesNotCallPaperServiceInRealModeEvenWhenTimeoutIsConfigured() {
        OrderService orders = mock(OrderService.class);
        PaperOrderExpiryJob job = new PaperOrderExpiryJob(
                new PaperTradingProperties(BigDecimal.ZERO, BigDecimal.ZERO, 30), orders,
                new TradingSafetyProperties("real", true, true, new BigDecimal("1000000"), new BigDecimal("100")));

        job.expireStaleOrders();

        verify(orders, never()).expireStalePaperOrders(30);
    }

    @Test
    void doesNotSweepWhenTimeoutIsDisabled() {
        OrderService orders = mock(OrderService.class);
        PaperOrderExpiryJob job = new PaperOrderExpiryJob(
                new PaperTradingProperties(BigDecimal.ZERO, BigDecimal.ZERO, 0), orders,
                new TradingSafetyProperties("paper", false, false, new BigDecimal("1000000"), new BigDecimal("100")));

        job.expireStaleOrders();

        verify(orders, never()).expireStalePaperOrders(0);
    }
}
