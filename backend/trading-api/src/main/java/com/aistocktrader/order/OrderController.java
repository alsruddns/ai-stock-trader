package com.aistocktrader.order;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import java.util.List;
import java.util.UUID;
import java.time.LocalDate;

@RestController
@RequestMapping("/api/v1/orders")
public class OrderController {
    private final OrderService service;
    private final PaperDailyRiskBaselineService riskBaselineService;
    public OrderController(OrderService service, PaperDailyRiskBaselineService riskBaselineService) {
        this.service = service;
        this.riskBaselineService = riskBaselineService;
    }
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

    @PostMapping("/paper/{orderId}/amend")
    public OrderRecord amendPaperOrder(@PathVariable Long orderId,
                                       @RequestHeader("Idempotency-Key") UUID idempotencyKey,
                                       @Valid @RequestBody PaperOrderAmendmentRequest request) {
        return service.amendPaperOrder(orderId, idempotencyKey, request);
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

    @GetMapping("/paper/strategy-risks")
    public List<OrderService.StrategyPositionRiskView> paperStrategyRisks() {
        return service.paperStrategyRisks();
    }

    @GetMapping("/paper/risk-baselines/{sessionDate}")
    public List<PaperDailyRiskBaseline> paperRiskBaselines(@PathVariable LocalDate sessionDate) {
        return riskBaselineService.forSession(sessionDate);
    }

    @PutMapping("/paper/risk-baselines/{sessionDate}/{currency}")
    public PaperDailyRiskBaseline recordPaperRiskBaseline(@PathVariable LocalDate sessionDate,
                                                           @PathVariable Currency currency,
                                                           @Valid @RequestBody PaperDailyRiskBaselineRequest request) {
        return riskBaselineService.recordOnce(sessionDate, currency, request.equity());
    }
}
