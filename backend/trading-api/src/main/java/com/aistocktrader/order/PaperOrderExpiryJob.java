package com.aistocktrader.order;

import com.aistocktrader.config.PaperTradingProperties;
import com.aistocktrader.config.TradingSafetyProperties;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class PaperOrderExpiryJob {
    private final PaperTradingProperties properties;
    private final OrderService orders;
    private final TradingSafetyProperties safety;

    public PaperOrderExpiryJob(PaperTradingProperties properties, OrderService orders, TradingSafetyProperties safety) {
        this.properties = properties;
        this.orders = orders;
        this.safety = safety;
    }

    @Scheduled(fixedDelay = 10_000)
    public void expireStaleOrders() {
        if ("paper".equals(safety.mode()) && !safety.liveTradingEnabled()
                && properties.orderTimeoutSeconds() > 0) {
            orders.expireStalePaperOrders(properties.orderTimeoutSeconds());
        }
    }
}
