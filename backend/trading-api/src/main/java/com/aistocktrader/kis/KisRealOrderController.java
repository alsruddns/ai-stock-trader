package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.OrderRequest;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Locale;
import java.util.UUID;

/** Local REAL order API. Every write path remains behind the live mode and account-read gates. */
@RestController
@RequestMapping("/api/v1/kis/orders/real")
public class KisRealOrderController {
    private final KisRealOrderOrchestrator orders;
    private final KisOrderIntentStore intents;
    private final KisAccountClient accounts;
    private final KisMarketAiEvidenceClient marketAiEvidence;
    private final KisProperties kisProperties;
    private final TradingSafetyProperties safety;

    @Autowired
    public KisRealOrderController(KisRealOrderOrchestrator orders, KisOrderIntentStore intents,
                                  KisAccountClient accounts, KisProperties kisProperties,
                                  TradingSafetyProperties safety, KisMarketAiEvidenceClient marketAiEvidence) {
        this.orders = orders;
        this.intents = intents;
        this.accounts = accounts;
        this.marketAiEvidence = marketAiEvidence;
        this.kisProperties = kisProperties;
        this.safety = safety;
    }

    /** Compatibility constructor for tests that exercise only the locked/raw-order paths. */
    public KisRealOrderController(KisRealOrderOrchestrator orders, KisOrderIntentStore intents,
                                  KisAccountClient accounts, KisProperties kisProperties,
                                  TradingSafetyProperties safety) {
        this(orders, intents, accounts, kisProperties, safety, null);
    }

    @PostMapping
    public ResponseEntity<KisOrderIntentResponse> submit(
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @RequestParam(required = false) String exchange,
            @Valid @RequestBody OrderRequest request) {
        requireRealOrderPathEnabled(request.side() == com.aistocktrader.order.OrderSide.BUY);
        String normalizedExchange = exchange == null ? null : exchange.trim().toUpperCase(Locale.ROOT);
        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, request.market(), request.symbol(),
                request.limitPrice(), normalizedExchange);
        KisOrderIntentResponse response = orders.submit(idempotencyKey, request, normalizedExchange, account);
        HttpStatus status = response.status() == KisOrderStatus.REJECTED
                ? HttpStatus.UNPROCESSABLE_ENTITY : HttpStatus.ACCEPTED;
        return ResponseEntity.status(status).body(response);
    }

    @PostMapping("/signals")
    public ResponseEntity<KisOrderIntentResponse> submitSignal(
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @RequestParam(required = false) String exchange,
            @Valid @RequestBody KisRealSignalRequest signal) {
        requireRealOrderPathEnabled(true);
        String normalizedExchange = exchange == null ? null : exchange.trim().toUpperCase(Locale.ROOT);
        if (signal.market() == com.aistocktrader.order.Market.US && !validUsExchange(normalizedExchange)) {
            throw new KisApiException(HttpStatus.UNPROCESSABLE_ENTITY, "A supported US exchange is required.");
        }
        if (marketAiEvidence == null) {
            throw new KisApiException(HttpStatus.LOCKED, "REAL signal submission requires the local market-data service.");
        }
        com.aistocktrader.order.Currency currency = signal.market() == com.aistocktrader.order.Market.KR
                ? com.aistocktrader.order.Currency.KRW : com.aistocktrader.order.Currency.USD;
        OrderRequest probe = new OrderRequest(signal.market(), signal.symbol(),
                com.aistocktrader.order.OrderSide.BUY, Integer.MAX_VALUE, java.math.BigDecimal.ONE, currency);
        KisMarketAiEvidenceClient.Evidence evidence = marketAiEvidence.inspect(probe, normalizedExchange);
        if (!evidence.approvedModelCandidate() || !evidence.quoteFresh() || evidence.quotePrice() == null
                || evidence.quotePrice().signum() <= 0 || evidence.modelId() == null || evidence.strategyId() == null
                || evidence.stopDistancePerShare() == null || evidence.stopDistancePerShare().signum() <= 0) {
            throw new KisApiException(HttpStatus.LOCKED,
                    "REAL signal submission requires a fresh approved-model candidate and trusted quote evidence.");
        }
        OrderRequest request = new OrderRequest(signal.market(), signal.symbol(),
                com.aistocktrader.order.OrderSide.BUY, Integer.MAX_VALUE, evidence.quotePrice(), currency,
                evidence.strategyId(), evidence.modelId());
        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, signal.market(), signal.symbol(),
                evidence.quotePrice(), normalizedExchange);
        KisOrderIntentResponse response = orders.submit(idempotencyKey, request, normalizedExchange, account);
        HttpStatus status = response.status() == KisOrderStatus.REJECTED
                ? HttpStatus.UNPROCESSABLE_ENTITY : HttpStatus.ACCEPTED;
        return ResponseEntity.status(status).body(response);
    }

    @GetMapping("/{idempotencyKey}")
    public KisOrderIntentResponse get(@PathVariable UUID idempotencyKey) {
        return KisOrderIntentResponse.from(realIntent(idempotencyKey));
    }

    @PostMapping("/{idempotencyKey}/reconcile")
    public ResponseEntity<KisOrderIntentResponse> reconcile(@PathVariable UUID idempotencyKey) {
        requireRealOrderPathEnabled(false);
        realIntent(idempotencyKey);
        return ResponseEntity.ok(orders.reconcile(idempotencyKey));
    }

    @PostMapping("/{idempotencyKey}/cancel")
    public ResponseEntity<KisOrderIntentResponse> cancel(@PathVariable UUID idempotencyKey) {
        requireRealOrderPathEnabled(false);
        realIntent(idempotencyKey);
        return ResponseEntity.accepted().body(orders.cancel(idempotencyKey));
    }

    private KisOrderIntent realIntent(UUID idempotencyKey) {
        KisOrderIntent intent = intents.get(idempotencyKey);
        if (intent.getEnvironment() != KisEnvironment.REAL) {
            throw new KisApiException(HttpStatus.NOT_FOUND, "REAL KIS order intent was not found.");
        }
        return intent;
    }

    private void requireRealOrderPathEnabled(boolean requiresProtectiveExitManager) {
        if (!"real".equals(safety.mode()) || !safety.liveTradingEnabled() || !safety.realOrdersEnabled()
                || !kisProperties.accountReadEnabled() || !kisProperties.realAccountReadEnabled()) {
            throw new KisApiException(HttpStatus.LOCKED,
                    "REAL order path is locked; live trading and both REAL account-read switches must be enabled.");
        }
        if (requiresProtectiveExitManager && !safety.realProtectiveExitManagerEnabled()) {
            throw new KisApiException(HttpStatus.LOCKED,
                    "REAL entries require the verified protective exit manager to be enabled.");
        }
    }

    private static boolean validUsExchange(String exchange) {
        return exchange != null && java.util.List.of("NASD", "NAS", "NYSE", "NYS", "AMEX", "AMS")
                .contains(exchange);
    }
}
