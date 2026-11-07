package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.OrderRequest;
import jakarta.validation.Valid;
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
    private final KisProperties kisProperties;
    private final TradingSafetyProperties safety;

    public KisRealOrderController(KisRealOrderOrchestrator orders, KisOrderIntentStore intents,
                                  KisAccountClient accounts, KisProperties kisProperties,
                                  TradingSafetyProperties safety) {
        this.orders = orders;
        this.intents = intents;
        this.accounts = accounts;
        this.kisProperties = kisProperties;
        this.safety = safety;
    }

    @PostMapping
    public ResponseEntity<KisOrderIntentResponse> submit(
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @RequestParam(required = false) String exchange,
            @Valid @RequestBody OrderRequest request) {
        requireRealOrderPathEnabled();
        String normalizedExchange = exchange == null ? null : exchange.trim().toUpperCase(Locale.ROOT);
        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, request.market(), request.symbol(),
                request.limitPrice(), normalizedExchange);
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
        requireRealOrderPathEnabled();
        realIntent(idempotencyKey);
        return ResponseEntity.ok(orders.reconcile(idempotencyKey));
    }

    @PostMapping("/{idempotencyKey}/cancel")
    public ResponseEntity<KisOrderIntentResponse> cancel(@PathVariable UUID idempotencyKey) {
        requireRealOrderPathEnabled();
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

    private void requireRealOrderPathEnabled() {
        if (!"real".equals(safety.mode()) || !safety.liveTradingEnabled() || !safety.realOrdersEnabled()
                || !kisProperties.accountReadEnabled() || !kisProperties.realAccountReadEnabled()) {
            throw new KisApiException(HttpStatus.LOCKED,
                    "REAL order path is locked; live trading and both REAL account-read switches must be enabled.");
        }
    }
}
