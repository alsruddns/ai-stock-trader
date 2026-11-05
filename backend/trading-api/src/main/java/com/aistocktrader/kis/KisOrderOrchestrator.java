package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.UUID;

@Service
public class KisOrderOrchestrator {
    private final KisOrderClient orders;
    private final KisAccountClient accounts;
    private final KisOrderProperties orderProperties;
    private final TradingSafetyProperties safety;
    private final KisOrderIntentStore intents;

    public KisOrderOrchestrator(KisOrderClient orders, KisAccountClient accounts,
                                KisOrderProperties orderProperties, TradingSafetyProperties safety,
                                KisOrderIntentStore intents) {
        this.orders = orders; this.accounts = accounts; this.orderProperties = orderProperties;
        this.safety = safety; this.intents = intents;
    }

    public KisOrderIntentResponse submitPaperOrder(UUID idempotencyKey, OrderRequest request, String exchange) {
        if (!orderProperties.paperEnabled()) throw new IllegalStateException("KIS paper orders are disabled.");
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("KIS orders are locked outside paper mode.");
        }
        String requestHash = hash(request, exchange);
        KisOrderIntentStore.PreparedIntent prepared;
        try {
            prepared = intents.prepare(idempotencyKey, requestHash, request);
        } catch (org.springframework.dao.DataIntegrityViolationException duplicateRace) {
            prepared = new KisOrderIntentStore.PreparedIntent(intents.get(idempotencyKey), false);
        }
        KisOrderIntent intent = prepared.intent();
        if (!prepared.createdNow()) {
            if (intent.getStatus() == KisOrderStatus.SUBMITTING) intent = intents.markUnknown(idempotencyKey);
            return KisOrderIntentResponse.from(intent);
        }

        try {
            reconcileAccount(request, exchange);
            KisOrderReceipt receipt = orders.placePaperLimitOrder(request.market(), request.symbol(), request.side(),
                    request.quantity(), request.limitPrice(), request.currency(), exchange);
            return KisOrderIntentResponse.from(intents.accept(idempotencyKey, receipt));
        } catch (KisOrderOutcomeUnknownException ex) {
            return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey));
        } catch (KisApiException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, extractCode(ex.getMessage())));
        } catch (IllegalArgumentException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_BLOCKED"));
        } catch (IllegalStateException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_DISABLED"));
        }
    }

    public KisOrderIntentResponse cancelPaperOrder(UUID idempotencyKey, String exchange) {
        if (!orderProperties.paperEnabled()) throw new IllegalStateException("KIS paper orders are disabled.");
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("KIS orders are locked outside paper mode.");
        }
        KisOrderIntent intent = intents.get(idempotencyKey);
        if (intent.getStatus() == KisOrderStatus.CANCEL_PENDING || intent.getStatus() == KisOrderStatus.CANCELLED) {
            return KisOrderIntentResponse.from(intent);
        }
        intent = intents.markCancelPending(idempotencyKey); // durable before the external request
        try {
            String normalizedExchange = intent.getMarket() == Market.US ? normalizeExchange(exchange) : "";
            orders.cancelPaperOrder(intent.getMarket(), intent.getSymbol(), intent.getBrokerOrderNumber(),
                    intent.getBrokerOrganizationNumber(), 0, normalizedExchange);
        } catch (KisOrderOutcomeUnknownException ex) {
            // Keep CANCEL_PENDING; never send a second cancel without broker reconciliation.
        }
        return KisOrderIntentResponse.from(intents.get(idempotencyKey));
    }

    private void reconcileAccount(OrderRequest request, String exchange) {
        BigDecimal notional = request.limitPrice().multiply(BigDecimal.valueOf(request.quantity()));
        BigDecimal maximum = request.currency() == Currency.KRW ? safety.maxOrderNotionalKrw() : safety.maxOrderNotionalUsd();
        if (notional.compareTo(maximum) > 0) throw new IllegalArgumentException("KIS paper order exceeds the configured notional limit.");
        String kisExchange = request.market() == Market.US ? normalizeExchange(exchange) : "";
        KisAccountSnapshot snapshot = accounts.getBalance(KisEnvironment.PAPER, request.market(),
                request.symbol(), request.limitPrice(), kisExchange);
        if (request.side() == OrderSide.BUY) {
            if (snapshot.orderableCash() == null || notional.compareTo(snapshot.orderableCash()) > 0) {
                throw new IllegalArgumentException("KIS paper account has insufficient orderable cash.");
            }
        } else {
            BigDecimal available = snapshot.positions().stream().filter(position -> position.symbol().equalsIgnoreCase(request.symbol()))
                    .map(KisAccountSnapshot.Position::sellableQuantity).filter(java.util.Objects::nonNull)
                    .findFirst().orElse(BigDecimal.ZERO);
            if (available.compareTo(BigDecimal.valueOf(request.quantity())) < 0) {
                throw new IllegalArgumentException("KIS paper account has insufficient sellable quantity.");
            }
        }
    }

    private String hash(OrderRequest request, String exchange) {
        String canonical = request.market() + "|" + request.symbol().trim().toUpperCase(Locale.ROOT) + "|" + request.side()
                + "|" + request.quantity() + "|" + request.limitPrice().stripTrailingZeros().toPlainString()
                + "|" + request.currency() + "|" + (exchange == null ? "" : exchange.toUpperCase(Locale.ROOT));
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable.", exception);
        }
    }

    private static String normalizeExchange(String exchange) {
        if (exchange == null) throw new IllegalArgumentException("US orders require an exchange.");
        return switch (exchange.toUpperCase(Locale.ROOT)) {
            case "NAS", "NASD" -> "NASD";
            case "NYS", "NYSE" -> "NYSE";
            case "AMS", "AMEX" -> "AMEX";
            default -> throw new IllegalArgumentException("US exchange must be NAS, NYS, or AMS.");
        };
    }

    private static String extractCode(String message) {
        var match = java.util.regex.Pattern.compile("code ([A-Za-z0-9_-]{1,32})").matcher(message);
        return match.find() ? match.group(1) : "rejected";
    }
}
