package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.UUID;

/** Converts a vetted domestic PAPER signal into a Spring-sized KIS PAPER order. */
@RestController
@RequestMapping("/api/v1/kis/orders/paper/signals")
public class KisPaperSignalOrderController {
    private final KisOrderOrchestrator orders;
    private final KisAccountClient accounts;
    private final KisProperties kis;
    private final KisOrderProperties orderProperties;
    private final TradingSafetyProperties safety;
    private final KisMarketAiEvidenceClient marketEvidence;
    private final KisPaperSignalOrderSizer sizing;
    private final MarketOperationalScope marketScope;
    private final boolean submissionEnabled;
    private final boolean cancelPreflightVerified;
    private final boolean protectiveExitVerified;

    public KisPaperSignalOrderController(KisOrderOrchestrator orders, KisAccountClient accounts,
                                         KisProperties kis, KisOrderProperties orderProperties,
                                         TradingSafetyProperties safety, KisMarketAiEvidenceClient marketEvidence,
                                         KisPaperSignalOrderSizer sizing, MarketOperationalScope marketScope,
                                         @Value("${kis.orders.paper-signal-submission-enabled:false}") boolean submissionEnabled,
                                         @Value("${kis.orders.paper-cancel-preflight-verified:false}") boolean cancelPreflightVerified,
                                         @Value("${kis.orders.paper-protective-exit-verified:false}") boolean protectiveExitVerified) {
        this.orders = orders;
        this.accounts = accounts;
        this.kis = kis;
        this.orderProperties = orderProperties;
        this.safety = safety;
        this.marketEvidence = marketEvidence;
        this.sizing = sizing;
        this.marketScope = marketScope;
        this.submissionEnabled = submissionEnabled;
        this.cancelPreflightVerified = cancelPreflightVerified;
        this.protectiveExitVerified = protectiveExitVerified;
    }

    @PostMapping
    public ResponseEntity<KisOrderIntentResponse> submit(
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @Valid @RequestBody KisPaperSignalRequest signal) {
        marketScope.require(signal.market(), "KIS PAPER signal submission");
        requireSignalPathEnabled();
        if (signal.market() != Market.KR) {
            throw new KisApiException(HttpStatus.UNPROCESSABLE_ENTITY,
                    "KIS PAPER signal automation is currently limited to domestic stocks.");
        }

        OrderRequest probe = new OrderRequest(Market.KR, signal.symbol(), OrderSide.BUY,
                Integer.MAX_VALUE, BigDecimal.ONE, Currency.KRW);
        KisMarketAiEvidenceClient.Evidence evidence = marketEvidence.inspectPaper(probe);
        if (!evidence.approvedModelCandidate() || !evidence.quoteFresh() || evidence.quotePrice() == null
                || evidence.quotePrice().signum() <= 0 || evidence.modelId() == null || evidence.strategyId() == null
                || evidence.stopDistancePerShare() == null || evidence.stopDistancePerShare().signum() <= 0) {
            throw new KisApiException(HttpStatus.LOCKED,
                    "KIS PAPER signal requires a fresh PAPER quote and an approved executable model candidate.");
        }

        KisAccountSnapshot account = accounts.getBalance(KisEnvironment.PAPER, Market.KR,
                signal.symbol(), evidence.quotePrice(), "");
        int quantity;
        try {
            quantity = sizing.sizeDomesticBuy(signal.symbol(), evidence.quotePrice(),
                    evidence.stopDistancePerShare(), account);
        } catch (IllegalStateException rejected) {
            throw new KisApiException(HttpStatus.LOCKED, rejected.getMessage());
        }
        OrderRequest request = new OrderRequest(Market.KR, signal.symbol(), OrderSide.BUY,
                quantity, evidence.quotePrice(), Currency.KRW, evidence.strategyId(), evidence.modelId());
        KisOrderIntentResponse response = orders.submitPaperOrder(idempotencyKey, request, null);
        HttpStatus status = response.status() == KisOrderStatus.REJECTED
                ? HttpStatus.UNPROCESSABLE_ENTITY : HttpStatus.ACCEPTED;
        return ResponseEntity.status(status).body(response);
    }

    private void requireSignalPathEnabled() {
        if (!submissionEnabled || !orderProperties.paperEnabled() || !orderProperties.paperReconciliationEnabled()
                || !cancelPreflightVerified || !protectiveExitVerified
                || !kis.accountReadEnabled() || !kis.paperAccountReadEnabled()
                || !kis.credentials(KisEnvironment.PAPER).configured()
                || safety.liveTradingEnabled() || !"paper".equals(safety.mode())) {
            throw new KisApiException(HttpStatus.LOCKED,
                    "KIS PAPER signal orders require explicit submission, account-read, reconciliation, verified cancel-preflight, and protective-exit gates.");
        }
    }
}
