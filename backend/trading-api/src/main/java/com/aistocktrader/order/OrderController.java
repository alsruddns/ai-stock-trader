package com.aistocktrader.order;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import java.util.List;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {
    private final OrderService service;
    public OrderController(OrderService service) { this.service = service; }
    @PostMapping("/paper")
    @ResponseStatus(HttpStatus.CREATED)
    public OrderRecord paperOrder(@RequestHeader("Idempotency-Key") UUID idempotencyKey,
                                  @Valid @RequestBody OrderRequest request) {
        return service.submit(request, idempotencyKey);
    }

    @PostMapping("/paper/{orderId}/fills")
    public OrderRecord recordPaperFill(@PathVariable Long orderId, @Valid @RequestBody PaperFillRequest request) {
        return service.recordPaperFill(orderId, request);
    }

    @PostMapping("/paper/{orderId}/cancel")
    public OrderRecord cancelPaperOrder(@PathVariable Long orderId) {
        return service.cancelPaperOrder(orderId);
    }

    @PostMapping("/paper/{orderId}/expire")
    public OrderRecord expirePaperOrder(@PathVariable Long orderId) {
        return service.expirePaperOrder(orderId);
    }

    @GetMapping("/paper/{orderId}")
    public OrderRecord getPaperOrder(@PathVariable Long orderId) {
        return service.getPaperOrder(orderId);
    }

    @GetMapping("/paper/by-idempotency/{idempotencyKey}")
    public OrderRecord getPaperOrderByIdempotencyKey(@PathVariable UUID idempotencyKey) {
        return service.getPaperOrderByIdempotencyKey(idempotencyKey);
    }

    @GetMapping("/paper/positions")
    public List<PaperPosition> paperPositions() {
        return service.paperPositions();
    }

    @GetMapping("/paper/cash")
    public List<PaperCashBalance> paperCashBalances() {
        return service.paperCashBalances();
    }
}
