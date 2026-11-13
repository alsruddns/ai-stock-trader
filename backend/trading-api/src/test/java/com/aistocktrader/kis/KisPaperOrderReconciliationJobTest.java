package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class KisPaperOrderReconciliationJobTest {
    @Test
    void disabledReadOnlyPollingDoesNotQueryDatabaseOrBroker() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisOrderOrchestrator orchestrator = mock(KisOrderOrchestrator.class);

        job(new KisOrderProperties(false, false), kisProperties(true, true, true),
                paperSafety(), intents, orchestrator).reconcileKnownBrokerOrders();

        verifyNoInteractions(intents, orchestrator);
    }

    @Test
    void missingAccountReadSwitchOrCredentialsKeepsPollingIdle() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisOrderOrchestrator orchestrator = mock(KisOrderOrchestrator.class);

        job(new KisOrderProperties(false, true), kisProperties(false, true, true),
                paperSafety(), intents, orchestrator).reconcileKnownBrokerOrders();
        job(new KisOrderProperties(false, true), kisProperties(true, false, true),
                paperSafety(), intents, orchestrator).reconcileKnownBrokerOrders();
        job(new KisOrderProperties(false, true), kisProperties(true, true, false),
                paperSafety(), intents, orchestrator).reconcileKnownBrokerOrders();
        job(new KisOrderProperties(false, true), kisProperties(true, true, true),
                new TradingSafetyProperties("real", true, true, new BigDecimal("1000000"), new BigDecimal("100")),
                intents, orchestrator).reconcileKnownBrokerOrders();

        verifyNoInteractions(intents, orchestrator);
    }

    @Test
    void enabledReadOnlyPollingReconcilesOnlyKnownDomesticPaperOrders() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisOrderOrchestrator orchestrator = mock(KisOrderOrchestrator.class);
        UUID domesticKey = UUID.randomUUID();
        UUID foreignKey = UUID.randomUUID();
        UUID missingBrokerKey = UUID.randomUUID();
        KisOrderIntent domestic = acceptedOrder(domesticKey, Market.KR, "005930", "9101");
        KisOrderIntent foreign = acceptedOrder(foreignKey, Market.US, "AAPL", "9102");
        KisOrderIntent missingBroker = new KisOrderIntent(missingBrokerKey, "missing-broker",
                KisEnvironment.PAPER, request(Market.KR, "000660"), null);
        missingBroker.markUnknown();
        when(intents.findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.PAPER), anyCollection()))
                .thenReturn(List.of(foreign, missingBroker, domestic));

        job(new KisOrderProperties(false, true), kisProperties(true, true, true),
                paperSafety(), intents, orchestrator).reconcileKnownBrokerOrders();

        verify(intents).findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.PAPER), anyCollection());
        verify(orchestrator).reconcilePaperOrder(domesticKey);
        verify(orchestrator, never()).reconcilePaperOrder(foreignKey);
        verify(orchestrator, never()).reconcilePaperOrder(missingBrokerKey);
    }

    @Test
    void failedOrderReadDoesNotPreventReconcilingOtherDomesticOrders() {
        KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        KisOrderOrchestrator orchestrator = mock(KisOrderOrchestrator.class);
        UUID firstKey = UUID.randomUUID();
        UUID secondKey = UUID.randomUUID();
        KisOrderIntent first = acceptedOrder(firstKey, Market.KR, "005930", "9103");
        KisOrderIntent second = acceptedOrder(secondKey, Market.KR, "000660", "9104");
        when(intents.findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.PAPER), anyCollection()))
                .thenReturn(List.of(first, second));
        doThrow(new KisApiException(org.springframework.http.HttpStatus.BAD_GATEWAY, "safe failure"))
                .when(orchestrator).reconcilePaperOrder(firstKey);

        job(new KisOrderProperties(false, true), kisProperties(true, true, true),
                paperSafety(), intents, orchestrator).reconcileKnownBrokerOrders();

        verify(orchestrator).reconcilePaperOrder(firstKey);
        verify(orchestrator).reconcilePaperOrder(secondKey);
    }

    private static KisPaperOrderReconciliationJob job(KisOrderProperties orderProperties, KisProperties properties,
                                                      TradingSafetyProperties safety, KisOrderIntentRepository intents,
                                                      KisOrderOrchestrator orchestrator) {
        return new KisPaperOrderReconciliationJob(orderProperties, properties, safety,
                MarketOperationalScope.domesticOnly(), intents, orchestrator);
    }

    private static KisProperties kisProperties(boolean globalRead, boolean paperRead, boolean credentials) {
        return new KisProperties(globalRead, paperRead, false, false,
                credentials ? new KisProperties.Account("paper-key", "paper-secret", "1234567801")
                        : new KisProperties.Account("", "", ""),
                new KisProperties.Account("", "", ""), Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }

    private static TradingSafetyProperties paperSafety() {
        return new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100"));
    }

    private static OrderRequest request(Market market, String symbol) {
        boolean domestic = market == Market.KR;
        return new OrderRequest(market, symbol, OrderSide.BUY, 1,
                new BigDecimal(domestic ? "70000" : "100"), domestic ? Currency.KRW : Currency.USD);
    }

    private static KisOrderIntent acceptedOrder(UUID key, Market market, String symbol, String brokerOrderNumber) {
        KisOrderIntent intent = new KisOrderIntent(key, "hash-" + key, KisEnvironment.PAPER,
                request(market, symbol), null);
        intent.accept(new KisOrderReceipt("paper", market.name(), symbol, "BUY", brokerOrderNumber,
                "ORG01", "0", Instant.now()));
        return intent;
    }
}
