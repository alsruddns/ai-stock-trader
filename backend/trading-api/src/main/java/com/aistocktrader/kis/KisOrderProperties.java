package com.aistocktrader.kis;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "kis.orders")
public record KisOrderProperties(boolean paperEnabled, boolean paperReconciliationEnabled) { }
