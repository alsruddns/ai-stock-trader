package com.aistocktrader.order;

import com.aistocktrader.config.PaperTradingProperties;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class PaperCashInitializer implements ApplicationRunner {
    private final PaperCashRepository cash;
    private final PaperTradingProperties properties;

    public PaperCashInitializer(PaperCashRepository cash, PaperTradingProperties properties) {
        this.cash = cash;
        this.properties = properties;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        for (Currency currency : Currency.values()) {
            if (!cash.existsById(currency)) {
                cash.save(new PaperCashBalance(currency, properties.forCurrency(currency)));
            }
        }
    }
}
