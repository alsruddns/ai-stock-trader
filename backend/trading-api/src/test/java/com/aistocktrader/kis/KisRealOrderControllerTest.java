package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRealOrderControllerTest {
    private final KisRealOrderOrchestrator orders = mock(KisRealOrderOrchestrator.class);
    private final KisOrderIntentStore intents = mock(KisOrderIntentStore.class);
    private final KisAccountClient accounts = mock(KisAccountClient.class);
    private final KisMarketAiEvidenceClient marketAiEvidence = mock(KisMarketAiEvidenceClient.class);

    @Test
    void paperModeRejectsRealSubmitBeforeReadingAccountOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), null, buyRequest()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders, marketAiEvidence);
    }

    @Test
    void blocksOverseasRealSubmissionAtTheOperationalScopeGate() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));
        OrderRequest overseas = new OrderRequest(Market.US, "AAPL", OrderSide.BUY,
                1, BigDecimal.ONE, Currency.USD);

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), "NASD", overseas));

        assertEquals(HttpStatus.LOCKED, exception.status());
        org.junit.jupiter.api.Assertions.assertTrue(exception.getMessage().contains("domestic KR operational scope"));
        verifyNoInteractions(accounts, orders, marketAiEvidence);
    }

    @Test
    void paperModeRejectsRealSignalBeforeReadingEvidenceAccountOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submitSignal(UUID.randomUUID(), null, new KisRealSignalRequest(Market.KR, "005930")));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders, marketAiEvidence);
    }

    @Test
    void readOnlyPreflightWorksWhileAllOrderSwitchesRemainLocked() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false,
                        BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)), properties(true, true));
        OrderRequest request = buyRequest();
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000),
                List.of(), Instant.now(), "KIS_READ_ONLY_REST");
        KisRealOrderPreflightResponse expected = new KisRealOrderPreflightResponse(false,
                List.of("APPROVED_MODEL_CANDIDATE_REQUIRED"), request, Instant.now());
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(1000), null))
                .thenReturn(account);
        when(orders.preflight(request, null, account)).thenReturn(expected);

        KisRealOrderPreflightResponse actual = controller.preflight(request, null);

        assertEquals(expected, actual);
        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(1000), null);
        verify(orders).preflight(request, null, account);
        verify(orders, never()).submit(any(), any(), any(), any());
        verifyNoInteractions(intents, marketAiEvidence);
    }

    @Test
    void readOnlyPreflightRequiresOnlyAccountReadSwitches() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false,
                        BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)), properties(false, true));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.preflight(buyRequest(), null));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders, intents, marketAiEvidence);
    }

    @Test
    void paperModeRejectsRealReconciliationBeforeLoadingIntentOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.reconcile(UUID.randomUUID()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(intents, accounts, orders);
    }

    @Test
    void paperModeRejectsRealCancellationBeforeLoadingIntentOrCallingBroker() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("paper", false, false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, false));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.cancel(UUID.randomUUID()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(intents, accounts, orders);
    }

    @Test
    void domesticScopeBlocksOutOfScopeRealIntentReadReconciliationAndCancellationBeforeBrokerAccess() {
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true, true,
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100));
        KisRealOrderController controller = controller(safety, properties(true, true));
        UUID key = UUID.randomUUID();
        OrderRequest overseas = new OrderRequest(Market.US, "AAPL", OrderSide.BUY,
                1, BigDecimal.ONE, Currency.USD);
        when(intents.get(key)).thenReturn(new KisOrderIntent(key, "hash", KisEnvironment.REAL,
                overseas, "NASD"));

        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.get(key)).status());
        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.reconcile(key)).status());
        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.cancel(key)).status());

        verifyNoInteractions(accounts, orders, marketAiEvidence);
    }

    @Test
    void everyRealPathRequiresGlobalAccountReadSwitchEvenInRealMode() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("real", true, true, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(false, true));

        assertAllRealPathsLocked(controller);
    }

    @Test
    void everyRealPathRequiresRealAccountReadSwitchEvenInRealMode() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("real", true, true, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(true, false));

        assertAllRealPathsLocked(controller);
    }

    @Test
    void enabledRealPathReadsCurrentAccountBeforeDelegatingTheOrder() {
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true, true,
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100));
        KisProperties kis = properties(true, true);
        KisRealOrderController controller = controller(safety, kis);
        UUID key = UUID.randomUUID();
        OrderRequest request = buyRequest();
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000),
                List.of(), Instant.now(), "test");
        KisOrderIntentResponse expected = new KisOrderIntentResponse(key, "real", "KR", "005930", "BUY", 1,
                "1000", "KRW", KisOrderStatus.ACCEPTED, "broker-order", "broker-org", "0",
                0, 1, null, Instant.now(), Instant.now());
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(1000), null))
                .thenReturn(account);
        when(orders.submit(key, request, null, account)).thenReturn(expected);

        var response = controller.submit(key, null, request);

        assertEquals(HttpStatus.ACCEPTED, response.getStatusCode());
        assertEquals(expected, response.getBody());
        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(1000), null);
        verify(orders).submit(key, request, null, account);
    }

    @Test
    void realSellReadsPositionsWithoutRequestingBuyPower() {
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true,
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100));
        KisRealOrderController controller = controller(safety, properties(true, true));
        UUID key = UUID.randomUUID();
        OrderRequest sell = new OrderRequest(Market.KR, "005930", OrderSide.SELL, 1,
                BigDecimal.valueOf(70000), Currency.KRW);
        var position = new KisAccountSnapshot.Position("005930", "Samsung", BigDecimal.ONE, BigDecimal.ONE,
                BigDecimal.valueOf(70000), BigDecimal.valueOf(70000), BigDecimal.valueOf(70000),
                BigDecimal.ZERO, BigDecimal.ZERO);
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.valueOf(70000), List.of(position), Instant.now(),
                "KIS_READ_ONLY_REST");
        KisOrderIntentResponse expected = new KisOrderIntentResponse(key, "real", "KR", "005930", "SELL", 1,
                "70000", "KRW", KisOrderStatus.ACCEPTED, "broker-order", "broker-org", "0",
                0, 1, null, Instant.now(), Instant.now());
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, null, null, null)).thenReturn(account);
        when(orders.submit(key, sell, null, account)).thenReturn(expected);

        var response = controller.submit(key, null, sell);

        assertEquals(HttpStatus.ACCEPTED, response.getStatusCode());
        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, null, null, null);
        verify(orders).submit(key, sell, null, account);
    }

    @Test
    void realSignalUsesServerQuoteAndVerifiedMetadataInsteadOfCallerSuppliedSizing() {
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true, true,
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100));
        KisRealOrderController controller = controller(safety, properties(true, true));
        UUID key = UUID.randomUUID();
        KisRealSignalRequest signal = new KisRealSignalRequest(Market.KR, "005930");
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000),
                List.of(), Instant.now(), "KIS_READ_ONLY_REST");
        KisOrderIntentResponse expected = new KisOrderIntentResponse(key, "real", "KR", "005930", "BUY", 10,
                "70000", "KRW", KisOrderStatus.ACCEPTED, "broker-order", "broker-org", "0",
                0, 10, null, Instant.now(), Instant.now());
        when(marketAiEvidence.inspect(any(OrderRequest.class), isNull())).thenReturn(
                new KisMarketAiEvidenceClient.Evidence(true, true, BigDecimal.valueOf(100),
                        "verified-model", "ORB_VWAP", BigDecimal.valueOf(70_000)));
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, "005930", BigDecimal.valueOf(70_000), null))
                .thenReturn(account);
        when(orders.submit(eq(key), any(OrderRequest.class), isNull(), eq(account))).thenReturn(expected);

        var response = controller.submitSignal(key, null, signal);

        assertEquals(HttpStatus.ACCEPTED, response.getStatusCode());
        ArgumentCaptor<OrderRequest> submitted = ArgumentCaptor.forClass(OrderRequest.class);
        verify(orders).submit(eq(key), submitted.capture(), isNull(), eq(account));
        assertEquals(Integer.MAX_VALUE, submitted.getValue().quantity());
        assertEquals(BigDecimal.valueOf(70_000), submitted.getValue().limitPrice());
        assertEquals("verified-model", submitted.getValue().modelId());
        assertEquals("ORB_VWAP", submitted.getValue().strategyId());
    }

    @Test
    void signalWithoutFreshApprovedEvidenceIsRejectedBeforeReadingRealAccount() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("real", true, true, true, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(true, true));
        when(marketAiEvidence.inspect(any(OrderRequest.class), isNull())).thenReturn(
                new KisMarketAiEvidenceClient.Evidence(false, false, null));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submitSignal(UUID.randomUUID(), null, new KisRealSignalRequest(Market.KR, "005930")));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders);
    }

    @Test
    void realBuyEntryStaysLockedWithoutVerifiedProtectiveExitManager() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("real", true, true, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(true, true));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), null, buyRequest()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders, marketAiEvidence);
    }

    @Test
    void realBuyEntryStaysLockedWhenProtectiveAutomationOrReconciliationIsOff() {
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true, true,
                BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100));
        KisRealOrderController controller = new KisRealOrderController(orders, intents, accounts,
                properties(true, true), safety, marketAiEvidence, false, true);

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.submit(UUID.randomUUID(), null, buyRequest()));

        assertEquals(HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(accounts, orders, marketAiEvidence);
    }

    @Test
    void realSellRemainsAvailableWhenNewEntryGateIsClosed() {
        KisRealOrderController controller = controller(
                new TradingSafetyProperties("real", true, true, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)),
                properties(true, true));
        UUID key = UUID.randomUUID();
        OrderRequest sell = new OrderRequest(Market.KR, "005930", OrderSide.SELL, 1,
                BigDecimal.valueOf(1000), Currency.KRW);
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW",
                BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000), BigDecimal.valueOf(100_000),
                List.of(), Instant.now(), "test");
        KisOrderIntentResponse expected = new KisOrderIntentResponse(key, "real", "KR", "005930", "SELL", 1,
                "1000", "KRW", KisOrderStatus.ACCEPTED, "broker-order", "broker-org", "0",
                0, 1, null, Instant.now(), Instant.now());
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, null, null, null))
                .thenReturn(account);
        when(orders.submit(key, sell, null, account)).thenReturn(expected);

        var response = controller.submit(key, null, sell);

        assertEquals(HttpStatus.ACCEPTED, response.getStatusCode());
        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, null, null, null);
        verify(orders).submit(key, sell, null, account);
    }

    private KisRealOrderController controller(TradingSafetyProperties safety, KisProperties kis) {
        return new KisRealOrderController(orders, intents, accounts, kis, safety, marketAiEvidence, true, true);
    }

    private void assertAllRealPathsLocked(KisRealOrderController controller) {
        UUID key = UUID.randomUUID();
        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.submit(key, null, buyRequest())).status());
        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.submitSignal(key, null, new KisRealSignalRequest(Market.KR, "005930"))).status());
        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.reconcile(key)).status());
        assertEquals(HttpStatus.LOCKED, assertThrows(KisApiException.class,
                () -> controller.cancel(key)).status());
        verifyNoInteractions(intents, accounts, orders, marketAiEvidence);
    }

    private static KisProperties properties(boolean allReads, boolean realRead) {
        return new KisProperties(allReads, false, realRead, false,
                new KisProperties.Account("", "", ""), new KisProperties.Account("", "", ""),
                null, null, null);
    }

    private static OrderRequest buyRequest() {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                BigDecimal.valueOf(1000), Currency.KRW);
    }
}
