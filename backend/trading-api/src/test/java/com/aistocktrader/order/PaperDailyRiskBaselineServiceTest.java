package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class PaperDailyRiskBaselineServiceTest {
    @Mock PaperDailyRiskBaselineRepository repository;
    private final LocalDate sessionDate = LocalDate.of(2026, 11, 5);
    private final TradingSafetyProperties paper = new TradingSafetyProperties("paper", false,
            new BigDecimal("1000000"), new BigDecimal("100"));

    @Test
    void recordsBaselineOnlyOnceAndReturnsSameValueOnRetry() {
        BigDecimal equity = new BigDecimal("10000000.00");
        PaperDailyRiskBaseline stored = new PaperDailyRiskBaseline(sessionDate, Currency.KRW, equity);
        when(repository.findBySessionDateAndCurrency(sessionDate, Currency.KRW))
                .thenReturn(Optional.empty(), Optional.of(stored));
        when(repository.save(any(PaperDailyRiskBaseline.class))).thenAnswer(call -> call.getArgument(0));
        var service = new PaperDailyRiskBaselineService(repository, paper);

        assertEquals(equity, service.recordOnce(sessionDate, Currency.KRW, equity).getEquity());
        assertSame(stored, service.recordOnce(sessionDate, Currency.KRW, equity));
        verify(repository, times(1)).save(any(PaperDailyRiskBaseline.class));
    }

    @Test
    void rejectsChangingAnExistingDailyBaseline() {
        PaperDailyRiskBaseline stored = new PaperDailyRiskBaseline(sessionDate, Currency.USD, new BigDecimal("5000"));
        when(repository.findBySessionDateAndCurrency(sessionDate, Currency.USD)).thenReturn(Optional.of(stored));
        var service = new PaperDailyRiskBaselineService(repository, paper);

        assertThrows(IdempotencyConflictException.class,
                () -> service.recordOnce(sessionDate, Currency.USD, new BigDecimal("5100")));
        verify(repository, never()).save(any());
    }

    @Test
    void rejectsNonPositiveEquity() {
        var service = new PaperDailyRiskBaselineService(repository, paper);
        assertThrows(IllegalArgumentException.class,
                () -> service.recordOnce(sessionDate, Currency.KRW, BigDecimal.ZERO));
        verifyNoInteractions(repository);
    }
}
