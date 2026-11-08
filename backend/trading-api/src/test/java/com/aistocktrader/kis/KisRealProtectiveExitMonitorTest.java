package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRealProtectiveExitMonitorTest {
    private KisOrderIntentRepository intents;
    private KisAccountClient accounts;
    private KisMarketSessionClient sessions;
    private KisMarketAiEvidenceClient marketData;
    private KisRealOrderOrchestrator orders;
    private KisRealProtectiveExitMonitor monitor;
    private KisOrderIntent plan;

    @BeforeEach
    void setUp() {
        intents = mock(KisOrderIntentRepository.class);
        accounts = mock(KisAccountClient.class);
        sessions = mock(KisMarketSessionClient.class);
        marketData = mock(KisMarketAiEvidenceClient.class);
        orders = mock(KisRealOrderOrchestrator.class);
        TradingSafetyProperties safety = new TradingSafetyProperties("real", true, true, true,
                new BigDecimal("1000000"), new BigDecimal("100"));
        KisProperties kis = new KisProperties(true, false, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "1234567801"),
                Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofSeconds(1));
        monitor = new KisRealProtectiveExitMonitor(true, true, safety, kis, intents, accounts, sessions, marketData, orders);
        plan = armedPlan();
        when(intents.findTop50ByEnvironmentAndRealExitStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyList()))
                .thenReturn(List.of(plan));
        when(intents.findExitManagedRealBuyCandidates(KisEnvironment.REAL, Market.KR, "005930"))
                .thenReturn(List.of(plan));
        Instant now = Instant.now();
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(Market.KR,
                LocalDate.now(), true, true, now.minus(Duration.ofHours(2)), now.plus(Duration.ofHours(2)), now));
        when(marketData.quoteForExit(Market.KR, "005930", null)).thenReturn(
                new KisMarketAiEvidenceClient.ExitQuote(Market.KR, "005930", null,
                        new BigDecimal("94"), new BigDecimal("93.99"), new BigDecimal("94.01"), now));
        when(accounts.getBalance(eq(KisEnvironment.REAL), eq(Market.KR), isNull(), isNull(), isNull()))
                .thenReturn(account(2));
        when(intents.claimRealExit(eq(plan.getIdempotencyKey()), any(UUID.class), eq("STOP_LOSS"), eq(2),
                eq(new BigDecimal("93.99")))).thenReturn(1);
    }

    @Test
    void disabledAutomationDoesNotReadCandidatesOrCallBroker() {
        KisProperties kis = new KisProperties(true, false, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "1234567801"),
                Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofSeconds(1));
        KisRealProtectiveExitMonitor disabled = new KisRealProtectiveExitMonitor(false, true,
                new TradingSafetyProperties("real", true, true, true,
                        new BigDecimal("1000000"), new BigDecimal("100")), kis,
                intents, accounts, sessions, marketData, orders);

        disabled.monitorRealPositions();

        verifyNoInteractions(intents, accounts, sessions, marketData, orders);
    }

    @Test
    void automationStaysLockedWhenOrderReconciliationIsDisabled() {
        KisProperties kis = new KisProperties(true, false, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "1234567801"),
                Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofSeconds(1));
        KisRealProtectiveExitMonitor unreconciled = new KisRealProtectiveExitMonitor(true, false,
                new TradingSafetyProperties("real", true, true, true,
                        new BigDecimal("1000000"), new BigDecimal("100")), kis,
                intents, accounts, sessions, marketData, orders);

        unreconciled.monitorRealPositions();

        verifyNoInteractions(intents, accounts, sessions, marketData, orders);
    }

    @Test
    void stopLossClaimsOneDurableSellUsingFreshBestBid() {
        monitor.monitorRealPositions();

        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, null, null, null);
        verify(intents).claimRealExit(eq(plan.getIdempotencyKey()), any(UUID.class), eq("STOP_LOSS"), eq(2),
                eq(new BigDecimal("93.99")));
        verify(orders).submit(any(UUID.class), argThat(request -> request.market() == Market.KR
                && request.side() == OrderSide.SELL && request.quantity() == 2
                && request.limitPrice().compareTo(new BigDecimal("93.99")) == 0), isNull(), any());
    }

    @Test
    void sessionEndClaimsProtectiveSellDuringTheFinalFiveMinutes() {
        Instant now = Instant.now();
        Instant sessionClose = now.plus(Duration.ofMinutes(3));
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(Market.KR,
                LocalDate.now(), true, true, now.minus(Duration.ofHours(2)), sessionClose,
                now.minus(Duration.ofHours(2)), sessionClose, now));
        when(marketData.quoteForExit(Market.KR, "005930", null)).thenReturn(
                new KisMarketAiEvidenceClient.ExitQuote(Market.KR, "005930", null,
                        new BigDecimal("100"), new BigDecimal("99.99"), new BigDecimal("100.01"), now));
        when(intents.claimRealExit(eq(plan.getIdempotencyKey()), any(UUID.class), eq("SESSION_END"), eq(2),
                eq(new BigDecimal("99.99")))).thenReturn(1);

        monitor.monitorRealPositions();

        verify(intents).claimRealExit(eq(plan.getIdempotencyKey()), any(UUID.class), eq("SESSION_END"), eq(2),
                eq(new BigDecimal("99.99")));
        verify(accounts).getBalance(KisEnvironment.REAL, Market.KR, null, null, null);
        verify(orders).submit(any(UUID.class), argThat(request -> request.market() == Market.KR
                && request.side() == OrderSide.SELL && request.quantity() == 2
                && request.limitPrice().compareTo(new BigDecimal("99.99")) == 0), isNull(), any());
    }

    @Test
    void accountQuantityMismatchMovesPlanToManualReviewWithoutSubmitting() {
        when(accounts.getBalance(eq(KisEnvironment.REAL), eq(Market.KR), isNull(), isNull(), isNull()))
                .thenReturn(account(3));

        monitor.monitorRealPositions();

        verify(intents).markRealExitManualReview(plan.getIdempotencyKey());
        verify(intents, never()).claimRealExit(any(), any(), anyString(), anyInt(), any());
        verifyNoInteractions(orders);
    }

    @Test
    void pendingFilledExitClosesPlanWithoutSubmittingAgain() {
        UUID exitKey = UUID.randomUUID();
        plan = exitPendingPlan(exitKey);
        when(intents.findTop50ByEnvironmentAndRealExitStatusInOrderByUpdatedAtAsc(eq(KisEnvironment.REAL), anyList()))
                .thenReturn(List.of(plan));
        var sell = new KisOrderIntent(exitKey, "sell-hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.SELL, 2,
                        new BigDecimal("93.99"), Currency.KRW), null);
        sell.accept(new KisOrderReceipt("real", "KR", "005930", "SELL", "broker-1", "ORG01", "0", Instant.now()));
        sell.reconcile(2, 0, new BigDecimal("93.99"), false, false);
        when(intents.findById(exitKey)).thenReturn(Optional.of(sell));

        monitor.monitorRealPositions();

        verify(intents).closeRealExitPlan(any(UUID.class));
        verifyNoInteractions(orders);
    }

    private KisOrderIntent armedPlan() {
        KisOrderIntent item = new KisOrderIntent(UUID.randomUUID(), "entry-hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("100"), Currency.KRW), null, new BigDecimal("5"), new BigDecimal("2"));
        item.reconcile(2, 0, new BigDecimal("100"), false, false);
        return item;
    }

    private KisOrderIntent exitPendingPlan(UUID key) {
        UUID entryKey = UUID.randomUUID();
        KisOrderIntent item = armedPlanWithKey(entryKey);
        // The repository claim is native SQL; for this unit test update the durable plan via reload fixture.
        try {
            var field = KisOrderIntent.class.getDeclaredField("realExitStatus");
            field.setAccessible(true);
            field.set(item, KisRealExitStatus.EXIT_PENDING);
            var keyField = KisOrderIntent.class.getDeclaredField("realExitOrderKey");
            keyField.setAccessible(true);
            keyField.set(item, key);
            var reasonField = KisOrderIntent.class.getDeclaredField("realExitReason");
            reasonField.setAccessible(true);
            reasonField.set(item, "STOP_LOSS");
            var quantityField = KisOrderIntent.class.getDeclaredField("realExitQuantity");
            quantityField.setAccessible(true);
            quantityField.set(item, 2);
            var priceField = KisOrderIntent.class.getDeclaredField("realExitLimitPrice");
            priceField.setAccessible(true);
            priceField.set(item, new BigDecimal("93.99"));
        } catch (ReflectiveOperationException exception) {
            throw new AssertionError(exception);
        }
        return item;
    }

    private KisOrderIntent armedPlanWithKey(UUID key) {
        KisOrderIntent item = new KisOrderIntent(key, "entry-hash", KisEnvironment.REAL,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("100"), Currency.KRW), null, new BigDecimal("5"), new BigDecimal("2"));
        item.reconcile(2, 0, new BigDecimal("100"), false, false);
        return item;
    }

    private KisAccountSnapshot account(int quantity) {
        Instant now = Instant.now();
        var held = new KisAccountSnapshot.Position("005930", "Samsung", BigDecimal.valueOf(quantity),
                BigDecimal.valueOf(quantity), new BigDecimal("100"), new BigDecimal("94"),
                new BigDecimal("188"), BigDecimal.ZERO, BigDecimal.ZERO);
        return new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("10000"),
                new BigDecimal("10000"), new BigDecimal("10000"), List.of(held), now, "KIS_READ_ONLY_REST");
    }
}
