package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.*;

class KisPaperSignalOrderControllerTest {
    @Test
    void paperSignalPathIsLockedByDefaultAndDoesNotReadAccountOrSubmit() {
        KisOrderOrchestrator orders = mock(KisOrderOrchestrator.class);
        KisAccountClient accounts = mock(KisAccountClient.class);
        KisMarketAiEvidenceClient evidence = mock(KisMarketAiEvidenceClient.class);
        KisPaperSignalOrderSizer sizing = mock(KisPaperSignalOrderSizer.class);
        KisProperties properties = new KisProperties(false, false, false, false,
                new KisProperties.Account("key", "secret", "1234567801"),
                new KisProperties.Account("", "", ""), null, null, null);
        var controller = new KisPaperSignalOrderController(orders, accounts, properties,
                new KisOrderProperties(false, false),
                new TradingSafetyProperties("paper", false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                evidence, sizing, MarketOperationalScope.domesticOnly(), false, false, false, false);

        KisApiException failure = assertThrows(KisApiException.class, () -> controller.submit(
                UUID.randomUUID(), new KisPaperSignalRequest(com.aistocktrader.order.Market.KR, "005930")));

        assertEquals(org.springframework.http.HttpStatus.LOCKED, failure.status());
        verifyNoInteractions(accounts, evidence, sizing, orders);
    }

    @Test
    void signalPathRemainsLockedUntilProtectiveExitAutomationIsEnabled() {
        KisOrderOrchestrator orders = mock(KisOrderOrchestrator.class);
        KisAccountClient accounts = mock(KisAccountClient.class);
        KisMarketAiEvidenceClient evidence = mock(KisMarketAiEvidenceClient.class);
        KisPaperSignalOrderSizer sizing = mock(KisPaperSignalOrderSizer.class);
        KisProperties properties = new KisProperties(true, true, false, false,
                new KisProperties.Account("key", "secret", "1234567801"),
                new KisProperties.Account("", "", ""), null, null, null);
        var controller = new KisPaperSignalOrderController(orders, accounts, properties,
                new KisOrderProperties(true, true),
                new TradingSafetyProperties("paper", false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                evidence, sizing, MarketOperationalScope.domesticOnly(), true, true, true, false);

        KisApiException failure = assertThrows(KisApiException.class, () -> controller.submit(
                UUID.randomUUID(), new KisPaperSignalRequest(com.aistocktrader.order.Market.KR, "005930")));

        assertEquals(org.springframework.http.HttpStatus.LOCKED, failure.status());
        verifyNoInteractions(accounts, evidence, sizing, orders);
    }
}
