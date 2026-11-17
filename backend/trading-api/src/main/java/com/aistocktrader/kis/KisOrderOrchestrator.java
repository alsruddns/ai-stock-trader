package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;

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
    private final KisMarketAiEvidenceClient marketEvidence;

    public KisOrderOrchestrator(KisOrderClient orders, KisAccountClient accounts,
                                KisOrderProperties orderProperties, TradingSafetyProperties safety,
                                KisOrderIntentStore intents, KisMarketAiEvidenceClient marketEvidence) {
        this.orders = orders; this.accounts = accounts; this.orderProperties = orderProperties;
        this.safety = safety; this.intents = intents; this.marketEvidence = marketEvidence;
    }

    public KisOrderIntentResponse submitPaperOrder(UUID idempotencyKey, OrderRequest request, String exchange) {
        return submitPaperOrder(idempotencyKey, request, exchange, null, null);
    }

    public KisOrderIntentResponse submitPaperSignalOrder(UUID idempotencyKey, OrderRequest request,
                                                          BigDecimal stopDistancePerShare) {
        if (stopDistancePerShare == null || stopDistancePerShare.signum() <= 0) {
            throw new IllegalArgumentException("A positive PAPER signal stop distance is required.");
        }
        return submitPaperOrder(idempotencyKey, request, null, stopDistancePerShare, new BigDecimal("2"));
    }

    private KisOrderIntentResponse submitPaperOrder(UUID idempotencyKey, OrderRequest request, String exchange,
                                                     BigDecimal exitStopDistancePerShare,
                                                     BigDecimal exitRewardRiskMultiple) {
        if (!orderProperties.paperEnabled()) throw new IllegalStateException("KIS paper orders are disabled.");
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("KIS orders are locked outside paper mode.");
        }
        KisDomesticOrderPriceValidator.validate(request.market(), request.symbol(), request.limitPrice());
        String requestHash = exitStopDistancePerShare == null
                ? hash(request, exchange) : hash(request, exchange, exitStopDistancePerShare, exitRewardRiskMultiple);
        KisOrderIntentStore.PreparedIntent prepared;
        try {
            prepared = preparePaperIntent(idempotencyKey, requestHash, request, exchange,
                    exitStopDistancePerShare, exitRewardRiskMultiple);
        } catch (org.springframework.dao.DataIntegrityViolationException duplicateRace) {
            try {
                // Re-run the store's hash and environment checks after the concurrent insert wins.
                prepared = preparePaperIntent(idempotencyKey, requestHash, request, exchange,
                        exitStopDistancePerShare, exitRewardRiskMultiple);
            } catch (org.springframework.dao.DataIntegrityViolationException reservationConflict) {
                if (isPaperMarketReservationConflict(reservationConflict)) {
                    throw new KisApiException(HttpStatus.LOCKED,
                            "An unresolved PAPER order is already reserved for this symbol.");
                }
                throw reservationConflict;
            }
        }
        KisOrderIntent intent = prepared.intent();
        if (!prepared.createdNow()) {
            if (intent.getStatus() == KisOrderStatus.SUBMITTING) intent = intents.markUnknown(idempotencyKey);
            return KisOrderIntentResponse.from(intent);
        }

        if (request.market() == Market.KR && orderProperties.paperHistoryCancelPreflightEnabled()) {
            try {
                orders.verifyDomesticPaperOrderHistoryAvailableForCancel(request.market(), request.symbol());
            } catch (KisApiException ex) {
                return KisOrderIntentResponse.from(intents.reject(idempotencyKey,
                        "PAPER_CANCEL_HISTORY_PREFLIGHT_FAILED"));
            } catch (IllegalArgumentException | IllegalStateException ex) {
                return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_BLOCKED"));
            }
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
            marketEvidence.requireFreshPaperOrderQuote(request, exchange);
        } catch (RestClientException | IllegalArgumentException | IllegalStateException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PAPER_QUOTE_PREFLIGHT_FAILED"));
        }

        try {
            KisOrderReceipt receipt = orders.placePaperLimitOrder(request.market(), request.symbol(), request.side(),
                    request.quantity(), request.limitPrice(), request.currency(), exchange);
            return KisOrderIntentResponse.from(intents.accept(idempotencyKey, receipt));
        } catch (KisOrderOutcomeUnknownException ex) {
            return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey, ex.diagnosticCode()));
        } catch (KisApiException ex) {
            if (KisResponseCodes.indicatesIndeterminateOrderOutcome(ex.getMessage())) {
                // A transient KIS server error does not prove the broker rejected the order.
                // Keep the idempotency key unresolved; never permit a fresh automatic submission.
                String responseCode = KisResponseCodes.fromErrorMessage(ex.getMessage(), "ORDER_OUTCOME_UNKNOWN");
                return KisOrderIntentResponse.from(intents.markUnknown(idempotencyKey, responseCode));
            }
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey,
                    KisResponseCodes.fromErrorMessage(ex.getMessage(), "KIS_ORDER_REJECTED")));
        } catch (IllegalArgumentException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_BLOCKED"));
        } catch (IllegalStateException ex) {
            return KisOrderIntentResponse.from(intents.reject(idempotencyKey, "PREFLIGHT_DISABLED"));
        }
    }

    private KisOrderIntentStore.PreparedIntent preparePaperIntent(UUID key, String requestHash,
                                                                   OrderRequest request, String exchange,
                                                                   BigDecimal stopDistancePerShare,
                                                                   BigDecimal rewardRiskMultiple) {
        if (stopDistancePerShare == null) return intents.prepare(key, requestHash, request, exchange);
        return intents.prepare(key, requestHash, KisEnvironment.PAPER, request, exchange,
                stopDistancePerShare, rewardRiskMultiple);
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

    public KisOrderIntentResponse manuallyReconcilePaperOrder(UUID idempotencyKey,
                                                              String brokerOrderNumber,
                                                              String brokerOrganizationNumber) {
        KisOrderIntent intent = intents.get(idempotencyKey);
        requirePaperIntent(intent);
        if (brokerOrderNumber != null && brokerOrderNumber.equals(intent.getBrokerOrderNumber())
                && (brokerOrganizationNumber == null || brokerOrganizationNumber.isBlank()
                        || brokerOrganizationNumber.equalsIgnoreCase(intent.getBrokerOrganizationNumber()))) {
            if (intent.getStatus() != KisOrderStatus.UNKNOWN) return KisOrderIntentResponse.from(intent);
            var knownOrderSnapshot = orders.reconcilePaperOrder(intent).orElseThrow(() ->
                    new KisApiException(HttpStatus.CONFLICT,
                            "KIS could not confirm the linked PAPER order; the intent remains UNKNOWN."));
            return KisOrderIntentResponse.from(intents.reconcile(idempotencyKey, knownOrderSnapshot));
        }
        if (intent.getStatus() != KisOrderStatus.UNKNOWN
                || intent.getBrokerOrderNumber() != null && !intent.getBrokerOrderNumber().isBlank()) {
            throw new IllegalStateException("Manual broker matching is only available for an unlinked UNKNOWN PAPER intent.");
        }
        var match = orders.reconcilePaperOrderByVerifiedCandidate(
                intent, brokerOrderNumber, brokerOrganizationNumber);
        if (match.isEmpty()) {
            throw new KisApiException(HttpStatus.CONFLICT,
                    "KIS did not confirm an exact matching PAPER order; the intent remains UNKNOWN.");
        }
        KisManualPaperOrderMatch verified = match.get();
        return KisOrderIntentResponse.from(intents.attachVerifiedPaperOrderAndReconcile(
                idempotencyKey, brokerOrderNumber, verified.brokerOrganizationNumber(), verified.snapshot()));
    }

    public KisOrderIntentResponse confirmNoDomesticPaperOrder(UUID idempotencyKey, boolean operatorConfirmedAbsent) {
        if (!operatorConfirmedAbsent) {
            throw new IllegalArgumentException("Confirm that KIS PAPER app shows no order before closing this intent.");
        }
        KisOrderIntent intent = intents.get(idempotencyKey);
        requirePaperIntent(intent);
        if (intent.getMarket() != Market.KR || intent.getStatus() != KisOrderStatus.UNKNOWN
                || intent.getBrokerOrderNumber() != null && !intent.getBrokerOrderNumber().isBlank()
                || (intent.getFilledQuantity() != null && intent.getFilledQuantity() != 0)
                || intent.getRemainingQuantity() == null || intent.getRemainingQuantity() != intent.getQuantity()) {
            throw new IllegalStateException("Only an unfilled, unlinked domestic PAPER UNKNOWN intent can be confirmed absent.");
        }
        if (intent.getCreatedAt().isAfter(java.time.Instant.now().minusSeconds(120))) {
            throw new IllegalStateException("Wait at least two minutes after submission before confirming no order.");
        }
        if (!orders.confirmNoDomesticPaperOrderInHistory(intent)) {
            throw new KisApiException(HttpStatus.CONFLICT,
                    "KIS order history contains a row for this symbol; the UNKNOWN intent remains locked.");
        }
        return KisOrderIntentResponse.from(intents.confirmNoPaperOrder(idempotencyKey));
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
        return sha256(canonical);
    }

    private static String sha256(String canonical) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable.", exception);
        }
    }

    static String hash(OrderRequest request, String exchange, BigDecimal stopDistancePerShare,
                       BigDecimal rewardRiskMultiple) {
        if (stopDistancePerShare == null || rewardRiskMultiple == null) {
            throw new IllegalArgumentException("Exit plan values are required for a PAPER signal hash.");
        }
        String canonical = request.market() + "|" + request.symbol().trim().toUpperCase(Locale.ROOT) + "|" + request.side()
                + "|" + request.quantity() + "|" + request.limitPrice().stripTrailingZeros().toPlainString()
                + "|" + request.currency() + "|" + (exchange == null ? "" : exchange.toUpperCase(Locale.ROOT))
                + "|strategy=" + (request.strategyId() == null ? "" : request.strategyId())
                + "|model=" + (request.modelId() == null ? "" : request.modelId())
                + "|exit=" + stopDistancePerShare.stripTrailingZeros().toPlainString()
                + "|rr=" + rewardRiskMultiple.stripTrailingZeros().toPlainString();
        return sha256(canonical);
    }

    private static boolean isPaperMarketReservationConflict(
            org.springframework.dao.DataIntegrityViolationException exception) {
        for (Throwable cause = exception; cause != null; cause = cause.getCause()) {
            if (cause instanceof org.hibernate.exception.ConstraintViolationException constraint
                    && "uq_kis_paper_unresolved_order_per_market_symbol".equals(constraint.getConstraintName())) {
                return true;
            }
            if (cause instanceof org.postgresql.util.PSQLException postgres
                    && postgres.getServerErrorMessage() != null
                    && "uq_kis_paper_unresolved_order_per_market_symbol"
                            .equals(postgres.getServerErrorMessage().getConstraint())) {
                return true;
            }
        }
        return false;
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
