package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.domain.PageImpl;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class KisPaperProtectiveExitMonitorTest {
    @Test
    void defaultDisabledAutomationDoesNotReadOrSubmit() {
        Dependencies d = new Dependencies();
        KisPaperProtectiveExitMonitor monitor = d.monitor(false, true, true);

        monitor.monitorDomesticPaperExits();

        verifyNoInteractions(d.plans, d.intents, d.accounts, d.sessions, d.marketData, d.orders);
    }

    @Test
    void freshPaperStopAndExactSellablePositionCreateOneDurableExitOrder() {
        Dependencies d = new Dependencies();
        UUID entryKey = UUID.randomUUID();
        KisPaperExitPlan plan = mock(KisPaperExitPlan.class);
        when(plan.getEntryIdempotencyKey()).thenReturn(entryKey);
        when(plan.getStatus()).thenReturn(KisPaperExitStatus.ARMED);
        KisOrderIntent entry = new KisOrderIntent(entryKey, "entry-hash", KisEnvironment.PAPER,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("70000"), Currency.KRW, "ORB_VWAP", "model-a"), null,
                new BigDecimal("1000"), new BigDecimal("2"));
        entry.reconcile(1, 0, new BigDecimal("70000"), false, false);

        Instant now = Instant.now();
        KisMarketSessionClient.MarketSession session = new KisMarketSessionClient.MarketSession(
                Market.KR, LocalDate.now(), true, true, now.minusSeconds(3600), now.plusSeconds(1800),
                now.minusSeconds(1));
        KisMarketAiEvidenceClient.ExitQuote quote = new KisMarketAiEvidenceClient.ExitQuote(
                Market.KR, "005930", null, new BigDecimal("68900"), new BigDecimal("68800"),
                new BigDecimal("68900"), now.minusSeconds(1));
        KisAccountSnapshot account = new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW",
                BigDecimal.ZERO, BigDecimal.ZERO, List.of(new KisAccountSnapshot.Position("005930", "Samsung",
                BigDecimal.ONE, BigDecimal.ONE, new BigDecimal("70000"), new BigDecimal("68900"),
                new BigDecimal("68900"), new BigDecimal("-1100"), new BigDecimal("-1.57"))),
                now.minusSeconds(1), "KIS_READ_ONLY_REST");
        when(d.plans.findByStatusInOrderByUpdatedAtAsc(anyList(), any()))
                .thenReturn(new PageImpl<>(List.of(plan)));
        when(d.plans.countActiveForSymbol("PAPER", "KR", "005930")).thenReturn(1L);
        when(d.plans.claimExit(eq(entryKey), any(UUID.class), eq("STOP_LOSS"), eq(1), eq(new BigDecimal("68800"))))
                .thenReturn(1);
        when(d.intents.findById(entryKey)).thenReturn(java.util.Optional.of(entry));
        when(d.sessions.session(Market.KR)).thenReturn(session);
        when(d.marketData.quoteForExit(KisEnvironment.PAPER, Market.KR, "005930", null)).thenReturn(quote);
        when(d.accounts.getBalance(KisEnvironment.PAPER, Market.KR, null, null, "")).thenReturn(account);

        d.monitor(true, true, true).monitorDomesticPaperExits();

        ArgumentCaptor<UUID> exitKey = ArgumentCaptor.forClass(UUID.class);
        verify(d.plans).claimExit(eq(entryKey), exitKey.capture(), eq("STOP_LOSS"), eq(1), eq(new BigDecimal("68800")));
        ArgumentCaptor<OrderRequest> request = ArgumentCaptor.forClass(OrderRequest.class);
        verify(d.orders).submitPaperOrder(eq(exitKey.getValue()), request.capture(), eq(null));
        assertEquals(OrderSide.SELL, request.getValue().side());
        assertEquals("005930", request.getValue().symbol());
        assertEquals(1, request.getValue().quantity());
        assertEquals(new BigDecimal("68800"), request.getValue().limitPrice());
        verify(d.marketData).quoteForExit(KisEnvironment.PAPER, Market.KR, "005930", null);
        verify(d.orders, never()).submitPaperOrder(eq(entryKey), any(OrderRequest.class), any());
    }

    @Test
    void armedPlanWithStillOpenPartialEntryIsMovedToManualReview() {
        Dependencies d = new Dependencies();
        UUID entryKey = UUID.randomUUID();
        KisPaperExitPlan plan = mock(KisPaperExitPlan.class);
        when(plan.getEntryIdempotencyKey()).thenReturn(entryKey);
        when(plan.getStatus()).thenReturn(KisPaperExitStatus.ARMED);
        KisOrderIntent entry = new KisOrderIntent(entryKey, "entry-hash", KisEnvironment.PAPER,
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW, "ORB_VWAP", "model-a"), null,
                new BigDecimal("1000"), new BigDecimal("2"));
        entry.reconcile(1, 1, new BigDecimal("70000"), false, false);
        when(d.plans.findByStatusInOrderByUpdatedAtAsc(anyList(), any()))
                .thenReturn(new PageImpl<>(List.of(plan)));
        when(d.intents.findById(entryKey)).thenReturn(java.util.Optional.of(entry));

        d.monitor(true, true, true).monitorDomesticPaperExits();

        verify(d.plans).requireManualReview(entryKey);
        verifyNoInteractions(d.accounts, d.sessions, d.marketData, d.orders);
    }

    @Test
    void missingVerifiedCancelPreflightKeepsExitAutomationIdle() {
        Dependencies d = new Dependencies();

        d.monitor(true, true, false).monitorDomesticPaperExits();

        verifyNoInteractions(d.plans, d.intents, d.accounts, d.sessions, d.marketData, d.orders);
    }

    private static final class Dependencies {
        final KisOrderProperties orderProperties = mock(KisOrderProperties.class);
        final KisProperties kis = new KisProperties(true, true, false, false,
                new KisProperties.Account("test-key", "test-secret", "1234567801"),
                new KisProperties.Account("", "", ""), Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
        final TradingSafetyProperties safety = new TradingSafetyProperties("paper", false, false, false,
                new BigDecimal("1000000"), new BigDecimal("100"));
        final KisPaperExitPlanRepository plans = mock(KisPaperExitPlanRepository.class);
        final KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        final KisAccountClient accounts = mock(KisAccountClient.class);
        final KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        final KisMarketAiEvidenceClient marketData = mock(KisMarketAiEvidenceClient.class);
        final KisOrderOrchestrator orders = mock(KisOrderOrchestrator.class);

        KisPaperProtectiveExitMonitor monitor(boolean enabled, boolean verified, boolean cancelPreflightVerified) {
            when(orderProperties.paperEnabled()).thenReturn(true);
            when(orderProperties.paperReconciliationEnabled()).thenReturn(true);
            return new KisPaperProtectiveExitMonitor(enabled, verified, cancelPreflightVerified,
                    orderProperties, kis, safety,
                    MarketOperationalScope.domesticOnly(), plans, intents, accounts, sessions, marketData, orders);
        }
    }
}
