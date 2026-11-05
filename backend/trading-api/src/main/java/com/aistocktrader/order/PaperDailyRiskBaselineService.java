package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

@Service
public class PaperDailyRiskBaselineService {
    private final PaperDailyRiskBaselineRepository baselines;
    private final TradingSafetyProperties safety;

    public PaperDailyRiskBaselineService(PaperDailyRiskBaselineRepository baselines, TradingSafetyProperties safety) {
        this.baselines = baselines;
        this.safety = safety;
    }

    @Transactional(readOnly = true)
    public List<PaperDailyRiskBaseline> forSession(LocalDate sessionDate) {
        requirePaperMode();
        return baselines.findAllBySessionDateOrderByCurrencyAsc(sessionDate);
    }

    @Transactional
    public PaperDailyRiskBaseline recordOnce(LocalDate sessionDate, Currency currency, BigDecimal equity) {
        requirePaperMode();
        if (sessionDate == null || currency == null || equity == null || equity.signum() <= 0) {
            throw new IllegalArgumentException("Session date, currency, and positive equity are required.");
        }
        return baselines.findBySessionDateAndCurrency(sessionDate, currency)
                .map(existing -> {
                    if (existing.getEquity().compareTo(equity) != 0) {
                        throw new IdempotencyConflictException("Daily risk baseline is immutable once recorded.");
                    }
                    return existing;
                })
                .orElseGet(() -> baselines.save(new PaperDailyRiskBaseline(sessionDate, currency, equity)));
    }

    private void requirePaperMode() {
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("Paper risk baselines are disabled outside paper mode.");
        }
    }
}
