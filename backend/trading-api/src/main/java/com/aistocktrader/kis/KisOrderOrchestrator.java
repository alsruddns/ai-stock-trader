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
    private static final String PAPER_CANCEL_PREFLIGHT_UNSUPPORTED = "PAPER_CANCEL_UNSUPPORTED";
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
        KisDomesticOrderPriceValidator.validate(request.market(), request.symbol(), request.limitPrice());
        String requestHash = hash(request, exchange);
        KisOrderIntentStore.PreparedIntent prepared;
        try {
            prepared = intents.prepare(idempotencyKey, requestHash, request, exchange);
        } catch (org.springframework.dao.DataIntegrityViolationException duplicateRace) {
            // Re-run the store's hash and environment checks after the concurrent insert wins.
            prepared = intents.prepare(idempotencyKey, requestHash, request, exchange);
        }
        KisOrderIntent intent = prepared.intent();
        if (!prepared.createdNow()) {
            if (intent.getStatus() == KisOrderStatus.SUBMITTING) intent = intents.markUnknown(idempotencyKey);
            return KisOrderIntentResponse.from(intent);
        }

        try {
            reconcileAccount(request, exchange);
        } catch (KisApiException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey,
                    KisResponseCodes.fromErrorMessage(ex.getMessage(), "ACCOUNT_PREFLIGHT_FAILED")));
        } catch (IllegalArgumentException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_BLOCKED"));
        } catch (IllegalStateException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_DISABLED"));
        }

        try {
            KisOrderReceipt receipt = orders.placePaperLimitOrder(request.market(), request.symbol(), request.side(),
                    request.quantity(), request.limitPrice(), request.currency(), exchange);
            return KisOrderIntentResponse.from(intents.accept(idempotencyKey, receipt));
        } catch (KisOrderOutcomeUnknownException ex) {
            return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey));
        } catch (KisApiException ex) {
            if (KisResponseCodes.indicatesIndeterminateOrderOutcome(ex.getMessage())) {
                // A transient KIS server error does not prove the broker rejected the order.
                // Keep the idempotency key unresolved; never permit a fresh automatic submission.
                return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey));
            }
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey,
                    KisResponseCodes.fromErrorMessage(ex.getMessage(), "KIS_ORDER_REJECTED")));
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
        requirePaperIntent(intent);
        if (intent.getStatus() == KisOrderStatus.CANCEL_PENDING || intent.getStatus() == KisOrderStatus.CANCELLED) {
            return KisOrderIntentResponse.from(intent);
        }
        if (intent.getStatus() == KisOrderStatus.SUBMITTING) {
            return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey));
        }
        if (intent.getStatus() == KisOrderStatus.FILLED || intent.getStatus() == KisOrderStatus.REJECTED
                || intent.getBrokerOrderNumber() == null || intent.getBrokerOrderNumber().isBlank()) {
            return KisOrderIntentResponse.from(intent);
        }
        KisBrokerOrderSnapshot observed = orders.reconcilePaperOrder(intent).orElse(null);
        if (!KisBrokerOrderSnapshotValidator.matchesOrderQuantity(intent, observed)) {
            return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey));
        }
        intent = intents.reconcile(idempotencyKey, observed);
        if (intent.getStatus() == KisOrderStatus.CANCELLED || intent.getStatus() == KisOrderStatus.FILLED
                || intent.getStatus() == KisOrderStatus.REJECTED || intent.getRemainingQuantity() <= 0) {
            return KisOrderIntentResponse.from(intent);
        }
        if (!intents.claimCancelPending(idempotencyKey, KisEnvironment.PAPER)) {
            // Another request already claimed cancellation, or the order became terminal.
            return KisOrderIntentResponse.from(intents.get(idempotencyKey));
        }
        intent = intents.get(idempotencyKey); // durable before the external request
        try {
            String normalizedExchange = intent.getMarket() == Market.US ? normalizeExchange(exchange) : "";
            orders.cancelPaperOrder(intent.getMarket(), intent.getSymbol(), intent.getBrokerOrderNumber(),
                    intent.getBrokerOrganizationNumber(), intent.getRemainingQuantity(), normalizedExchange);
        } catch (KisOrderOutcomeUnknownException ex) {
            // Keep CANCEL_PENDING; never send a second cancel without broker reconciliation.
        } catch (KisApiException ex) {
            String code = KisResponseCodes.fromErrorMessage(ex.getMessage(), "PAPER_CANCEL_REJECTED");
            if (PAPER_CANCEL_PREFLIGHT_UNSUPPORTED.equals(code)) {
                // The adapter rejected the operation before any KIS write. Restore the order's
                // already-observed broker state instead of leaving a false CANCEL_PENDING state.
                intents.recordCancelPreflightBlocked(idempotencyKey, code);
                return KisOrderIntentResponse.from(intents.reconcile(idempotencyKey, observed));
            }
            // A broker-declared rejection is definitive. Read the order once to restore its
            // observed state; if that query fails, retain CANCEL_PENDING and never resend.
            intent = intents.recordCancelRejection(idempotencyKey, code);
            try {
                KisBrokerOrderSnapshot snapshot = orders.reconcilePaperOrder(intent).orElse(null);
                if (snapshot != null) intent = intents.reconcile(idempotencyKey, snapshot);
            } catch (KisApiException ignored) {
                // Preserve the explicit rejection code and pending state for a later GET-only reconciliation.
            }
        }
        return KisOrderIntentResponse.from(intents.get(idempotencyKey));
    }

    public KisOrderIntentResponse reconcilePaperOrder(UUID idempotencyKey) {
        KisOrderIntent intent = intents.get(idempotencyKey);
        requirePaperIntent(intent);
        if (intent.getStatus() == KisOrderStatus.SUBMITTING) {
            intent = intents.markUnknown(idempotencyKey);
        }
        if (intent.getStatus() == KisOrderStatus.REJECTED || intent.getStatus() == KisOrderStatus.CANCELLED
                || intent.getStatus() == KisOrderStatus.FILLED) return KisOrderIntentResponse.from(intent);
        if (intent.getBrokerOrderNumber() == null || intent.getBrokerOrderNumber().isBlank()) {
            return KisOrderIntentResponse.from(intent);
        }
        return orders.reconcilePaperOrder(intent).map(snapshot ->
                KisOrderIntentResponse.from(intents.reconcile(idempotencyKey, snapshot)))
                .orElseGet(() -> KisOrderIntentResponse.from(intents.get(idempotencyKey)));
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

    static String hash(OrderRequest request, String exchange) {
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

    private static void requirePaperIntent(KisOrderIntent intent) {
        if (intent.getEnvironment() != KisEnvironment.PAPER) {
            throw new IllegalStateException("Paper order operations cannot access a real-environment order intent.");
        }
    }

}
