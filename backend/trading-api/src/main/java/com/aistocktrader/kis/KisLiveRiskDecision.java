package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Locale;

/** Short-lived server-side approval bound to the exact instrument, side, size, price, and exchange. */
public final class KisLiveRiskDecision {
    private static final Duration MAX_AGE = Duration.ofSeconds(3);
    private final boolean approved;
    private final List<String> reasons;
    private final String orderFingerprint;
    private final Instant assessedAt;

    KisLiveRiskDecision(boolean approved, List<String> reasons, OrderRequest request, String exchange) {
        this.approved = approved;
        this.reasons = List.copyOf(reasons);
        this.orderFingerprint = fingerprint(request, exchange);
        this.assessedAt = Instant.now();
    }

    public boolean approved() { return approved; }
    public List<String> reasons() { return reasons; }

    public boolean approves(OrderRequest request, String exchange) {
        if (!approved || request == null || !orderFingerprint.equals(fingerprint(request, exchange))) return false;
        Instant now = Instant.now();
        return !now.isBefore(assessedAt) && Duration.between(assessedAt, now).compareTo(MAX_AGE) <= 0;
    }

    private static String fingerprint(OrderRequest request, String exchange) {
        if (request == null) return "";
        Market market = request.market();
        Currency currency = request.currency();
        OrderSide side = request.side();
        BigDecimal price = request.limitPrice();
        return (market == null ? "" : market.name()) + "|"
                + (request.symbol() == null ? "" : request.symbol().trim().toUpperCase(Locale.ROOT)) + "|"
                + (side == null ? "" : side.name()) + "|" + request.quantity() + "|"
                + (price == null ? "" : price.stripTrailingZeros().toPlainString()) + "|"
                + (currency == null ? "" : currency.name()) + "|"
                + (exchange == null ? "" : exchange.trim().toUpperCase(Locale.ROOT));
    }
}
