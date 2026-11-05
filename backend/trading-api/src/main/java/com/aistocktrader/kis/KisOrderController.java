package com.aistocktrader.kis;

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

import java.util.UUID;

@RestController
@RequestMapping("/api/v1/kis/orders/paper")
public class KisOrderController {
    private final KisOrderOrchestrator orders;
    private final KisOrderIntentStore intents;

    public KisOrderController(KisOrderOrchestrator orders, KisOrderIntentStore intents) {
        this.orders = orders; this.intents = intents;
    }

    @PostMapping
    public ResponseEntity<KisOrderIntentResponse> submit(
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @RequestParam(required = false) String exchange,
            @Valid @RequestBody OrderRequest request) {
        KisOrderIntentResponse response = orders.submitPaperOrder(idempotencyKey, request, exchange);
        HttpStatus status = response.status() == KisOrderStatus.REJECTED ? HttpStatus.UNPROCESSABLE_ENTITY
                : response.status() == KisOrderStatus.ACCEPTED ? HttpStatus.ACCEPTED : HttpStatus.ACCEPTED;
        return ResponseEntity.status(status).body(response);
    }

    @GetMapping("/{idempotencyKey}")
    public KisOrderIntentResponse get(@PathVariable UUID idempotencyKey) {
        return KisOrderIntentResponse.from(intents.get(idempotencyKey));
    }

    @PostMapping("/{idempotencyKey}/reconcile")
    public ResponseEntity<KisOrderIntentResponse> reconcile(@PathVariable UUID idempotencyKey) {
        return ResponseEntity.ok(orders.reconcilePaperOrder(idempotencyKey));
    }

    @PostMapping("/{idempotencyKey}/cancel")
    public ResponseEntity<KisOrderIntentResponse> cancel(@PathVariable UUID idempotencyKey,
                                                         @RequestParam(required = false) String exchange) {
        return ResponseEntity.accepted().body(orders.cancelPaperOrder(idempotencyKey, exchange));
    }
}
