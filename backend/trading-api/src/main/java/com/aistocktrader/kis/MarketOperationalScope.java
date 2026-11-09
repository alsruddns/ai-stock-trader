package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;

/** Blocks live account and order operations outside the operator-approved market set. */
@Component
public final class MarketOperationalScope {
    private final Set<Market> allowedMarkets;

    public MarketOperationalScope(@Value("${trading.operational-markets:KR}") String configuredMarkets) {
        try {
            this.allowedMarkets = Arrays.stream(configuredMarkets.split(","))
                    .map(String::trim)
                    .filter(value -> !value.isEmpty())
                    .map(value -> Market.valueOf(value.toUpperCase()))
                    .collect(Collectors.toUnmodifiableSet());
        } catch (IllegalArgumentException ex) {
            throw new IllegalStateException("Trading operational market scope is invalid.");
        }
        if (allowedMarkets.isEmpty()) {
            throw new IllegalStateException("Trading operational market scope cannot be empty.");
        }
    }

    public static MarketOperationalScope domesticOnly() {
        return new MarketOperationalScope("KR");
    }

    public void require(Market market, String operation) {
        if (!allowedMarkets.contains(market)) {
            throw new KisApiException(HttpStatus.LOCKED,
                    operation + " is disabled outside the current domestic KR operational scope.");
        }
    }

    public Set<Market> allowedMarkets() {
        return allowedMarkets;
    }
}
