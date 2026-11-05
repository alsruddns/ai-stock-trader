package com.aistocktrader.order;

import org.springframework.data.jpa.repository.JpaRepository;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface PaperDailyRiskBaselineRepository extends JpaRepository<PaperDailyRiskBaseline, Long> {
    Optional<PaperDailyRiskBaseline> findBySessionDateAndCurrency(LocalDate sessionDate, Currency currency);
    List<PaperDailyRiskBaseline> findAllBySessionDateOrderByCurrencyAsc(LocalDate sessionDate);
}
