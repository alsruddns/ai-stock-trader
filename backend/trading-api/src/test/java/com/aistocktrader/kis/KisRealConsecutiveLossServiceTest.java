package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class KisRealConsecutiveLossServiceTest {
    private static final String FINGERPRINT = "a".repeat(64);

    @Test
    void verifiedEmptyLedgersAcrossBothMarketsProduceZeroLossesAndZeroPositions() {
        var fixture = new Fixture(true);
        when(fixture.accounts.getBalance(eq(KisEnvironment.REAL), eq(Market.US), any(), any(), any()))
                .thenReturn(account(Market.US, List.of()));

        var result = fixture.service.evaluate(Market.KR, account(Market.KR, List.of()));

        assertTrue(result.isPresent());
        assertEquals(new KisRealConsecutiveLossService.RiskHistory(0, 0), result.get());
    }

    @Test
    void domesticOnlyScopeCalculatesDomesticRiskWithoutCallingOverseasApis() {
        var fixture = new Fixture(true);
        var scopedService = new KisRealConsecutiveLossService(
                new KisProperties(true, true, true, true,
                        new KisProperties.Account("", "", ""),
                        new KisProperties.Account("app-key", "app-secret", "1234567890"),
                        Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofMillis(100)),
                fixture.accounts, fixture.sessions, fixture.positionBaselines, fixture.dailyRiskBaselines,
                fixture.intents, fixture.realOrders,
                new KisRealTradingCostProperties("0", "0", "0", "0", "0", "0"),
                MarketOperationalScope.domesticOnly());
        var domesticPosition = position("005930", "1");
        var openingPosition = new KisRealSessionPositionBaselineStore.Position(
                "005930", BigDecimal.ONE, BigDecimal.ONE);
        when(fixture.positionBaselines.get(eq(Market.KR), eq(LocalDate.now()), eq(FINGERPRINT))).thenReturn(Optional.of(
                new KisRealSessionPositionBaselineStore.PositionBaseline(Market.KR, LocalDate.now(), FINGERPRINT,
                        "KRW", Instant.now().minusSeconds(60), 1, "KIS_READ_ONLY_REST", List.of(openingPosition))));

        var result = scopedService.evaluate(Market.KR, account(Market.KR, List.of(domesticPosition)));
        var outOfScopeResult = scopedService.evaluate(Market.US, account(Market.US, List.of()));

        assertTrue(result.isPresent());
        assertEquals(new KisRealConsecutiveLossService.RiskHistory(0, 1), result.get());
        assertTrue(outOfScopeResult.isEmpty());
        verify(fixture.sessions, never()).session(Market.US);
        verify(fixture.sessions, never()).latestClosedSession(Market.US);
        verify(fixture.positionBaselines, never()).get(eq(Market.US), any(), anyString());
        verify(fixture.intents, never()).findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                eq(KisEnvironment.REAL), eq(Market.US), any());
        verify(fixture.realOrders, never()).getRealSessionExecutions(eq(Market.US), any());
        verify(fixture.accounts, never()).getBalance(eq(KisEnvironment.REAL), eq(Market.US), any(), any(), any());
    }

    @Test
    void usesLatestClosedSessionWhenOtherExchangeHasNoSessionOnItsLocalDate() {
        var fixture = new Fixture(true);
        Instant now = Instant.now();
        LocalDate latestUsDate = LocalDate.now().minusDays(3);
        Instant usOpen = now.minus(Duration.ofHours(7));
        var currentUsDate = new KisMarketSessionClient.MarketSession(Market.US, LocalDate.now(),
                false, false, null, null, now);
        var latestUsSession = new KisMarketSessionClient.MarketSession(Market.US, latestUsDate,
                true, false, usOpen, usOpen.plus(Duration.ofHours(6)).plus(Duration.ofMinutes(30)), now);
        when(fixture.sessions.session(Market.US)).thenReturn(currentUsDate);
        when(fixture.sessions.latestClosedSession(Market.US)).thenReturn(latestUsSession);
        when(fixture.positionBaselines.get(Market.US, latestUsDate, FINGERPRINT)).thenReturn(Optional.of(
                new KisRealSessionPositionBaselineStore.PositionBaseline(Market.US, latestUsDate, FINGERPRINT,
                        "USD", usOpen.plusSeconds(30), 0, "KIS_READ_ONLY_REST", List.of())));
        when(fixture.accounts.getBalance(eq(KisEnvironment.REAL), eq(Market.US), any(), any(), any()))
                .thenReturn(account(Market.US, List.of()));

        var result = fixture.service.evaluate(Market.KR, account(Market.KR, List.of()));

        assertTrue(result.isPresent());
        assertEquals(new KisRealConsecutiveLossService.RiskHistory(0, 0), result.get());
        verify(fixture.sessions).latestClosedSession(Market.US);
        verify(fixture.sessions, never()).session(Market.US);
    }

    @Test
    void accountInventoryMismatchBlocksStreakEvidence() {
        var fixture = new Fixture(true);
        when(fixture.accounts.getBalance(eq(KisEnvironment.REAL), eq(Market.US), any(), any(), any()))
                .thenReturn(account(Market.US, List.of(position("AAPL", "1"))));

        var result = fixture.service.evaluate(Market.KR, account(Market.KR, List.of()));

        assertTrue(result.isEmpty());
    }

    @Test
    void confirmedLosingRoundTripProducesOneConsecutiveNetLoss() {
        var fixture = new Fixture(true);
        Instant executionBase = Instant.now().minus(Duration.ofMinutes(1));
        KisOrderIntent buy = terminalIntent(OrderSide.BUY, "10", "BUY01", executionBase);
        KisOrderIntent sell = terminalIntent(OrderSide.SELL, "9", "SELL01", executionBase.plusSeconds(1));
        when(fixture.intents.findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                eq(KisEnvironment.REAL), eq(Market.KR), any(Instant.class))).thenReturn(List.of(buy, sell));
        when(fixture.realOrders.getRealSessionExecutions(eq(Market.KR), any(LocalDate.class))).thenReturn(List.of(
                new KisRealOrderClient.KisBrokerSessionExecution("BUY01", "005930", OrderSide.BUY, "ORG01"),
                new KisRealOrderClient.KisBrokerSessionExecution("SELL01", "005930", OrderSide.SELL, "ORG01")));
        when(fixture.accounts.getBalance(eq(KisEnvironment.REAL), eq(Market.US), any(), any(), any()))
                .thenReturn(account(Market.US, List.of()));

        var result = fixture.service.evaluate(Market.KR, account(Market.KR, List.of()));

        assertTrue(result.isPresent());
        assertEquals(1, result.get().consecutiveLosses());
        assertEquals(0, result.get().totalOpenPositionsAcrossMarkets());
    }

    @Test
    void brokerExecutionWithoutApplicationIntentMakesLossHistoryUnknown() {
        var fixture = new Fixture(true);
        when(fixture.realOrders.getRealSessionExecutions(eq(Market.KR), any(LocalDate.class))).thenReturn(List.of(
                new KisRealOrderClient.KisBrokerSessionExecution("MANUAL01", "005930", OrderSide.BUY, "ORG01")));

        assertTrue(fixture.service.evaluate(Market.KR, account(Market.KR, List.of())).isEmpty());
        verify(fixture.accounts, never()).getBalance(eq(KisEnvironment.REAL), eq(Market.US), any(), any(), any());
    }

    @Test
    void applicationFillMissingFromBrokerSessionHistoryMakesLossHistoryUnknown() {
        var fixture = new Fixture(true);
        KisOrderIntent buy = terminalIntent(OrderSide.BUY, "10", "BUY01", Instant.now().minusSeconds(10));
        when(fixture.intents.findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                eq(KisEnvironment.REAL), eq(Market.KR), any(Instant.class))).thenReturn(List.of(buy));

        assertTrue(fixture.service.evaluate(Market.KR, account(Market.KR, List.of())).isEmpty());
    }

    @Test
    void filledOrderWithoutBrokerExecutionTimeMakesLossHistoryUnknown() {
        var fixture = new Fixture(true);
        KisOrderIntent buy = terminalIntent(OrderSide.BUY, "10", "BUY01", null);
        when(fixture.intents.findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                eq(KisEnvironment.REAL), eq(Market.KR), any(Instant.class))).thenReturn(List.of(buy));

        assertTrue(fixture.service.evaluate(Market.KR, account(Market.KR, List.of())).isEmpty());
    }

    @Test
    void missingAccountSpecificCostScheduleBlocksBuyHistory() {
        var fixture = new Fixture(false);

        var result = fixture.service.evaluate(Market.KR, account(Market.KR, List.of()));

        assertTrue(result.isEmpty());
        verifyNoInteractions(fixture.accounts);
    }

    @Test
    void unresolvedOrderInEitherMarketMakesLossAndPositionHistoryUnknown() {
        var fixture = new Fixture(true);
        var unresolved = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL,
                new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 1, BigDecimal.ONE,
                        com.aistocktrader.order.Currency.USD), null);
        when(fixture.intents.findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                eq(KisEnvironment.REAL), eq(Market.US), any(Instant.class))).thenReturn(List.of(unresolved));

        var result = fixture.service.evaluate(Market.KR, account(Market.KR, List.of()));

        assertTrue(result.isEmpty());
        verifyNoInteractions(fixture.accounts);
    }

    private static KisAccountSnapshot account(Market market, List<KisAccountSnapshot.Position> positions) {
        return new KisAccountSnapshot(KisEnvironment.REAL, market, market == Market.KR ? "KRW" : "USD",
                BigDecimal.ZERO, BigDecimal.ZERO, positions, Instant.now(), "KIS_READ_ONLY_REST");
    }

    private static KisAccountSnapshot.Position position(String symbol, String quantity) {
        return new KisAccountSnapshot.Position(symbol, symbol, new BigDecimal(quantity), new BigDecimal(quantity),
                BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ONE, BigDecimal.ZERO, BigDecimal.ZERO);
    }

    private static KisOrderIntent terminalIntent(OrderSide side, String price, String brokerNumber, Instant fillAt) {
        var request = new OrderRequest(Market.KR, "005930", side, 1, new BigDecimal(price), Currency.KRW);
        var intent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL, request, null);
        intent.accept(new KisOrderReceipt("real", "KR", "005930", side.name(), brokerNumber, "ORG01",
                "0", Instant.now()));
        intent.reconcile(1, 0, new BigDecimal(price), false, false);
        intent.recordFillTime(fillAt);
        return intent;
    }

    private static class Fixture {
        final KisAccountClient accounts = mock(KisAccountClient.class);
        final KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        final KisRealSessionPositionBaselineStore positionBaselines = mock(KisRealSessionPositionBaselineStore.class);
        final KisRealDailyRiskBaselineService dailyRiskBaselines = mock(KisRealDailyRiskBaselineService.class);
        final KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
        final KisRealOrderClient realOrders = mock(KisRealOrderClient.class);
        final KisRealConsecutiveLossService service;

        Fixture(boolean costsConfigured) {
            Instant now = Instant.now();
            LocalDate date = LocalDate.now();
            for (Market market : Market.values()) {
                Instant open = now.minus(Duration.ofMinutes(2));
                var session = new KisMarketSessionClient.MarketSession(market, date,
                        true, true, open, open.plus(Duration.ofHours(7)), now);
                when(sessions.session(market)).thenReturn(session);
                when(sessions.latestClosedSession(market)).thenReturn(session);
                when(positionBaselines.get(eq(market), eq(date), eq(FINGERPRINT))).thenReturn(Optional.of(
                        new KisRealSessionPositionBaselineStore.PositionBaseline(market, date, FINGERPRINT,
                                market == Market.KR ? "KRW" : "USD", open.plusSeconds(30), 0,
                                "KIS_READ_ONLY_REST", List.of())));
                when(intents.findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                        eq(KisEnvironment.REAL), eq(market), any(Instant.class))).thenReturn(List.of());
                when(realOrders.getRealSessionExecutions(eq(market), eq(date))).thenReturn(List.of());
            }
            when(dailyRiskBaselines.accountFingerprint()).thenReturn(FINGERPRINT);
            var properties = new KisProperties(true, true, true, true,
                    new KisProperties.Account("", "", ""),
                    new KisProperties.Account("app-key", "app-secret", "1234567890"),
                    Duration.ofSeconds(3), Duration.ofSeconds(8),
                    Duration.ofMillis(100));
            String zero = costsConfigured ? "0" : "";
            var costs = new KisRealTradingCostProperties(zero, zero, zero, zero, zero, zero);
            service = new KisRealConsecutiveLossService(properties, accounts, sessions, positionBaselines,
                    dailyRiskBaselines, intents, realOrders, costs);
        }
    }
}
