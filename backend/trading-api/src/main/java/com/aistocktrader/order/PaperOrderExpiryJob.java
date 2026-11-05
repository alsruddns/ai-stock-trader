package com.aistocktrader.order;

import com.aistocktrader.config.PaperTradingProperties;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class PaperOrderExpiryJob {
    private final PaperTradingProperties properties;
    private final OrderService orders;

    public PaperOrderExpiryJob(PaperTradingProperties properties, OrderService orders) {
        this.properties = properties;
        this.orders = orders;
    }

    @Scheduled(fixedDelay = 10_000)
    public void expireStaleOrders() {
        if (properties.orderTimeoutSeconds() > 0) {
            orders.expireStalePaperOrders(properties.orderTimeoutSeconds());
        }
    }
}
