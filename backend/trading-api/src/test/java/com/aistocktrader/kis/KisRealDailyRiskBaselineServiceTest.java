package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verifyNoInteractions;
import org.mockito.ArgumentCaptor;

class KisRealDailyRiskBaselineServiceTest {
    @Test
    void skipsDomesticOpeningBaselineWhenKrIsOutsideOperationalScope() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisAccountClient accounts = mock(KisAccountClient.class);
        KisRealDailyRiskBaselineStore baselines = mock(KisRealDailyRiskBaselineStore.class);
        KisRealSessionPositionBaselineStore positionBaselines = mock(KisRealSessionPositionBaselineStore.class);
        KisProperties properties = new KisProperties(true, true, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "test-secret", "8765432101"), null, null, null);
        KisRealDailyRiskBaselineService service = new KisRealDailyRiskBaselineService(properties,
                new MarketOperationalScope("US"), accounts, sessions, baselines, positionBaselines);

        service.captureDomesticSessionBaseline();

        verifyNoInteractions(sessions, accounts, baselines, positionBaselines);
    }

    @Test
    void capturesDomesticOpeningEquityAndPositionsWithinKrOperationalScope() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisAccountClient accounts = mock(KisAccountClient.class);
        KisRealDailyRiskBaselineStore baselines = mock(KisRealDailyRiskBaselineStore.class);
        KisRealSessionPositionBaselineStore positionBaselines = mock(KisRealSessionPositionBaselineStore.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.KR, date, true, true, now.minusSeconds(60), now.plusSeconds(7_140), now));
        when(positionBaselines.exists(eq(Market.KR), eq(date), anyString())).thenReturn(false);
        when(baselines.exists(eq(Market.KR), eq(date), anyString())).thenReturn(false);
        var position = new KisAccountSnapshot.Position("005930", "Samsung Electronics", new BigDecimal("2"),
                new BigDecimal("2"), new BigDecimal("70000"), new BigDecimal("71000"),
                new BigDecimal("140000"), new BigDecimal("2000"), new BigDecimal("1.43"));
        var account = new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("500000"),
                new BigDecimal("500000"), new BigDecimal("1000000"), java.util.List.of(position), now,
                "KIS_READ_ONLY_REST");
        when(accounts.getBalance(KisEnvironment.REAL, Market.KR, null, null, null)).thenReturn(account);
        KisProperties properties = new KisProperties(true, true, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "test-secret", "8765432101"), null, null, null);
        KisRealDailyRiskBaselineService service = new KisRealDailyRiskBaselineService(properties,
                MarketOperationalScope.domesticOnly(), accounts, sessions, baselines, positionBaselines);

        service.captureDomesticSessionBaseline();

        verify(positionBaselines).recordOnce(eq(Market.KR), eq(date), eq("KRW"), anyString(),
                eq(java.util.List.of(position)), eq(now));
        verify(baselines).recordOnce(eq(Market.KR), eq(date), eq("KRW"), anyString(),
                eq(new BigDecimal("1000000")), eq(now));
    }

    @Test
    void domesticOnlyScopeSkipsOutOfScopeScheduledBaselineBeforeExternalAccess() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisAccountClient accounts = mock(KisAccountClient.class);
        KisRealDailyRiskBaselineStore baselines = mock(KisRealDailyRiskBaselineStore.class);
        KisRealSessionPositionBaselineStore positionBaselines = mock(KisRealSessionPositionBaselineStore.class);
        KisProperties properties = new KisProperties(true, true, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "test-secret", "8765432101"), null, null, null);
        KisRealDailyRiskBaselineService service = new KisRealDailyRiskBaselineService(properties,
                MarketOperationalScope.domesticOnly(), accounts, sessions, baselines, positionBaselines);

        service.captureUsSessionPositionBaseline();

        verifyNoInteractions(sessions, accounts, baselines, positionBaselines);
    }

    @Test
    void computesNonnegativeLossFromFreshKisEquityAndImmutableSessionBaseline() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealDailyRiskBaselineStore store = mock(KisRealDailyRiskBaselineStore.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.KR, date, true, true, now.minusSeconds(60), now.plusSeconds(7_140), now));
        when(store.equity(eq(Market.KR), eq(date), anyString()))
                .thenReturn(Optional.of(new BigDecimal("1000000")));
        KisRealDailyRiskBaselineService service = service(sessions, store);

        var loss = service.riskInputs(Market.KR, account(new BigDecimal("990000"), now));
        var profit = service.riskInputs(Market.KR, account(new BigDecimal("1010000"), now));

        assertEquals(new BigDecimal("1.00000000"), loss.dailyLossPercent());
        assertEquals(BigDecimal.ZERO, profit.dailyLossPercent());
        assertTrue(loss.marketOpen());
    }

    @Test
    void closesRealEntryWindowAtContinuousSessionEndBeforeOfficialClose() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealDailyRiskBaselineStore store = mock(KisRealDailyRiskBaselineStore.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        Instant officialClose = now.plusSeconds(60);
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.KR, date, true, true, now.minusSeconds(60), officialClose,
                now.minusSeconds(60), now.minusSeconds(1), now));
        when(store.equity(eq(Market.KR), eq(date), anyString()))
                .thenReturn(Optional.of(new BigDecimal("1000000")));
        KisRealDailyRiskBaselineService service = service(sessions, store);

        var risk = service.riskInputs(Market.KR, account(new BigDecimal("990000"), now));

        assertTrue(risk.marketOpen());
        assertFalse(risk.continuousEntryWindowOpen());
        assertEquals(new BigDecimal("1.00000000"), risk.dailyLossPercent());
    }

    @Test
    void missingBaselineStaleCalendarOrUnknownUsEquityFailClosed() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealDailyRiskBaselineStore store = mock(KisRealDailyRiskBaselineStore.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.KR, date, true, true, now.minusSeconds(60), now.plusSeconds(7_140), now));
        when(store.equity(eq(Market.KR), eq(date), anyString())).thenReturn(Optional.empty());
        when(sessions.session(Market.US)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.US, LocalDate.now(java.time.ZoneId.of("America/New_York")), true, true,
                now.minusSeconds(60), now.plusSeconds(7_140), now.minusSeconds(30)));
        KisRealDailyRiskBaselineService service = service(sessions, store);

        var noBaseline = service.riskInputs(Market.KR, account(new BigDecimal("1000000"), now));
        var staleCalendar = service.riskInputs(Market.US, accountUs(now));

        assertNull(noBaseline.dailyLossPercent());
        assertTrue(noBaseline.marketOpen());
        assertNull(staleCalendar.dailyLossPercent());
        assertFalse(staleCalendar.marketOpen());
    }

    @Test
    void baselineLookupUsesOnlyAKeyedFingerprintInsteadOfPersistingCredentials() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealDailyRiskBaselineStore store = mock(KisRealDailyRiskBaselineStore.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        when(sessions.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.KR, date, true, true, now.minusSeconds(60), now.plusSeconds(7_140), now));
        when(store.equity(eq(Market.KR), eq(date), anyString())).thenReturn(Optional.of(new BigDecimal("1000000")));
        KisRealDailyRiskBaselineService service = service(sessions, store);

        service.riskInputs(Market.KR, account(new BigDecimal("1000000"), now));

        ArgumentCaptor<String> fingerprint = ArgumentCaptor.forClass(String.class);
        verify(store).equity(eq(Market.KR), eq(date), fingerprint.capture());
        assertEquals(64, fingerprint.getValue().length());
        org.junit.jupiter.api.Assertions.assertFalse(fingerprint.getValue().contains("8765432101"));
        org.junit.jupiter.api.Assertions.assertFalse(fingerprint.getValue().contains("test-secret"));
    }

    @Test
    void capturesUsOpeningPositionsOnlyFromFreshKisReadOnlySnapshot() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealDailyRiskBaselineStore baselines = mock(KisRealDailyRiskBaselineStore.class);
        KisRealSessionPositionBaselineStore positionBaselines = mock(KisRealSessionPositionBaselineStore.class);
        KisAccountClient accounts = mock(KisAccountClient.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("America/New_York"));
        when(sessions.session(Market.US)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.US, date, true, true, now.minusSeconds(60), now.plusSeconds(7_140), now));
        when(positionBaselines.exists(eq(Market.US), eq(date), anyString())).thenReturn(false);
        var position = new KisAccountSnapshot.Position("AAPL", "Apple", new BigDecimal("2"),
                new BigDecimal("2"), new BigDecimal("190.25"), new BigDecimal("191"),
                new BigDecimal("382"), new BigDecimal("1.5"), new BigDecimal("0.39"));
        when(accounts.getBalance(KisEnvironment.REAL, Market.US, null, null, null)).thenReturn(
                new KisAccountSnapshot(KisEnvironment.REAL, Market.US, "USD", null,
                        new BigDecimal("1000"), null, java.util.List.of(position), now, "KIS_READ_ONLY_REST"));
        KisProperties properties = new KisProperties(true, true, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "test-secret", "8765432101"), null, null, null);
        KisRealDailyRiskBaselineService service = new KisRealDailyRiskBaselineService(properties,
                new MarketOperationalScope("KR,US"), accounts,
                sessions, baselines, positionBaselines);

        service.captureUsSessionPositionBaseline();

        verify(positionBaselines).recordOnce(eq(Market.US), eq(date), eq("USD"), anyString(),
                eq(java.util.List.of(position)), eq(now));
    }

    @Test
    void doesNotCaptureUsOpeningPositionsWhenSessionEvidenceIsStale() {
        KisMarketSessionClient sessions = mock(KisMarketSessionClient.class);
        KisRealDailyRiskBaselineStore baselines = mock(KisRealDailyRiskBaselineStore.class);
        KisRealSessionPositionBaselineStore positionBaselines = mock(KisRealSessionPositionBaselineStore.class);
        Instant now = Instant.now();
        LocalDate date = LocalDate.now(java.time.ZoneId.of("America/New_York"));
        when(sessions.session(Market.US)).thenReturn(new KisMarketSessionClient.MarketSession(
                Market.US, date, true, true, now.minusSeconds(60), now.plusSeconds(7_140), now.minusSeconds(30)));
        KisProperties properties = new KisProperties(true, true, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "test-secret", "8765432101"), null, null, null);
        KisRealDailyRiskBaselineService service = new KisRealDailyRiskBaselineService(properties,
                new MarketOperationalScope("KR,US"),
                mock(KisAccountClient.class), sessions, baselines, positionBaselines);

        service.captureUsSessionPositionBaseline();

        verify(positionBaselines, never()).recordOnce(eq(Market.US), eq(date), eq("USD"), anyString(),
                any(), any());
    }

    private static KisRealDailyRiskBaselineService service(KisMarketSessionClient sessions,
                                                             KisRealDailyRiskBaselineStore store) {
        KisProperties properties = new KisProperties(false, false, false, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "test-secret", "8765432101"),
                null, null, null);
        return new KisRealDailyRiskBaselineService(properties, MarketOperationalScope.domesticOnly(),
                mock(KisAccountClient.class), sessions, store,
                mock(KisRealSessionPositionBaselineStore.class));
    }

    private static KisAccountSnapshot account(BigDecimal equity, Instant retrievedAt) {
        return new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("100000"),
                new BigDecimal("100000"), equity, java.util.List.of(), retrievedAt, "KIS_READ_ONLY_REST");
    }

    private static KisAccountSnapshot accountUs(Instant retrievedAt) {
        return new KisAccountSnapshot(KisEnvironment.REAL, Market.US, "USD", null, new BigDecimal("100"),
                null, java.util.List.of(), retrievedAt, "KIS_READ_ONLY_REST");
    }
}
