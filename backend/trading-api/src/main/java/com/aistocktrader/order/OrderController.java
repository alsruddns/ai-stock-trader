package com.aistocktrader.order;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
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
}
