package com.aistocktrader.kis;

import com.aistocktrader.order.OrderRequest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.UUID;

/** Internal REAL order workflow. No public HTTP endpoint is wired to this service. */
@Service
public class KisRealOrderOrchestrator {
    private final KisRealOrderClient orders;
    private final KisLiveOrderRiskGate riskGate;
    private final KisOrderIntentStore intents;

    public KisRealOrderOrchestrator(KisRealOrderClient orders, KisLiveOrderRiskGate riskGate,
                                    KisOrderIntentStore intents) {
        this.orders = orders;
        this.riskGate = riskGate;
        this.intents = intents;
    }

    public KisOrderIntentResponse submit(UUID idempotencyKey, OrderRequest request, String exchange,
                                         KisAccountSnapshot realAccount, KisLiveRiskContext riskContext) {
        String requestHash = hash(request, exchange);
        KisLiveRiskDecision decision = riskGate.assess(request, exchange, realAccount, riskContext);
        KisOrderIntentStore.PreparedIntent prepared;
        try {
            prepared = intents.prepare(idempotencyKey, requestHash, KisEnvironment.REAL, request, exchange);
        } catch (DataIntegrityViolationException duplicateRace) {
            prepared = intents.prepare(idempotencyKey, requestHash, KisEnvironment.REAL, request, exchange);
        }
        if (!prepared.createdNow()) return resolveDuplicate(idempotencyKey, prepared.intent());

        if (!decision.approved()) {
            String reason = decision.reasons().isEmpty() ? "LIVE_RISK_REJECTED" : decision.reasons().getFirst();
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, reason));
        }

        try {
            var receipt = orders.placeRealLimitOrder(request.market(), request.symbol(), request.side(),
                    request.quantity(), request.limitPrice(), request.currency(), exchange, decision);
            return KisOrderIntentResponse.from(intents.accept(idempotencyKey, receipt));
        } catch (KisOrderOutcomeUnknownException exception) {
            return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey));
        } catch (KisApiException exception) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, extractCode(exception.getMessage())));
        } catch (IllegalArgumentException exception) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_BLOCKED"));
        } catch (IllegalStateException exception) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "REAL_ORDER_LOCKED"));
        }
    }

    public KisOrderIntentResponse cancel(UUID idempotencyKey) {
        KisOrderIntent intent = intents.get(idempotencyKey);
        requireRealIntent(intent);
        if (intent.getStatus() == KisOrderStatus.CANCEL_PENDING || intent.getStatus() == KisOrderStatus.CANCELLED) {
            return KisOrderIntentResponse.from(intent);
        }
        intent = intents.markCancelPending(idempotencyKey); // commit intent before contacting KIS
        try {
            orders.cancelRealOrder(intent.getMarket(), intent.getSymbol(), intent.getBrokerOrderNumber(),
                    intent.getBrokerOrganizationNumber(), intent.getRemainingQuantity() == null
                            ? intent.getQuantity() : intent.getRemainingQuantity(), intent.getExchangeCode());
        } catch (KisOrderOutcomeUnknownException exception) {
            // Keep CANCEL_PENDING; reconciliation must resolve the broker state before another request.
        }
        return KisOrderIntentResponse.from(intents.get(idempotencyKey));
    }

    public KisOrderIntentResponse reconcile(UUID idempotencyKey) {
        KisOrderIntent intent = intents.get(idempotencyKey);
        requireRealIntent(intent);
        if (intent.getStatus() == KisOrderStatus.SUBMITTING) {
            intent = intents.markUnknown(idempotencyKey);
        }
        if (intent.getStatus() == KisOrderStatus.REJECTED || intent.getStatus() == KisOrderStatus.CANCELLED
                || intent.getStatus() == KisOrderStatus.FILLED || intent.getBrokerOrderNumber() == null
                || intent.getBrokerOrderNumber().isBlank()) {
            return KisOrderIntentResponse.from(intent);
        }
        return orders.reconcileRealOrder(intent)
                .map(snapshot -> KisOrderIntentResponse.from(intents.reconcile(idempotencyKey, snapshot)))
                .orElseGet(() -> KisOrderIntentResponse.from(intents.get(idempotencyKey)));
    }

    private KisOrderIntentResponse resolveDuplicate(UUID idempotencyKey, KisOrderIntent intent) {
        requireRealIntent(intent);
        if (intent.getStatus() == KisOrderStatus.SUBMITTING) {
            intent = intents.markUnknown(idempotencyKey);
        }
        return KisOrderIntentResponse.from(intent);
    }

    private static void requireRealIntent(KisOrderIntent intent) {
        if (intent.getEnvironment() != KisEnvironment.REAL) {
            throw new IllegalStateException("REAL order operations cannot access a paper order intent.");
        }
    }

    private static String hash(OrderRequest request, String exchange) {
        if (request == null) throw new IllegalArgumentException("Order request is required.");
        String canonical = request.market() + "|" + request.symbol().trim().toUpperCase(Locale.ROOT) + "|" + request.side()
                + "|" + request.quantity() + "|" + request.limitPrice().stripTrailingZeros().toPlainString()
                + "|" + request.currency() + "|" + (exchange == null ? "" : exchange.trim().toUpperCase(Locale.ROOT));
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(canonical.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable.", exception);
        }
    }

    private static String extractCode(String message) {
        var match = java.util.regex.Pattern.compile("code ([A-Za-z0-9_-]{1,32})").matcher(message);
        return match.find() ? match.group(1) : "KIS_REJECTED";
    }
}
